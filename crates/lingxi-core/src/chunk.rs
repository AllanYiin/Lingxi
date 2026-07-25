//! 預切塊：把輸入依字元類別與規則切成「待分詞的中文塊」與「直接定案的詞段」。
//!
//! 規則刻意保守：只有 ASCII 錨定的樣式（URL、email、英數串、數字、
//! 「2014年」型時間詞）強制成段；中文數字與量詞序列留在 Han 塊內，
//! 由 DAG+HMM 依詞典機率決定（避免規則搶走「一起」「十分」等真詞）。
//! 所有 regex 以 LazyLock 靜態編譯一次（舊版在迴圈內 new Regex 是主要效能坑）。

use std::sync::LazyLock;

use regex::Regex;

/// 塊種類：Han 需進 DAG 分詞，其餘直接定案。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ChunkKind {
    /// 連續中文（含注音、〇、日文假名），交給 DAG+DP。
    Han,
    Url,
    Email,
    Eng,
    Num,
    Time,
    Punct,
    Space,
    Other,
}

/// 一個塊：byte 區間 + 種類。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Chunk {
    pub byte_start: usize,
    pub byte_end: usize,
    pub kind: ChunkKind,
}

/// Email 樣式（改寫自舊版 Constants.RegexEmail，去除 .NET (?n:) 模式）。
static EMAIL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)[a-z0-9_\-.]+@(\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.|([a-z0-9\-]+\.)+)([a-z][a-z0-9\-]{1,62}|[0-9]{1,3})\]?",
    )
    .unwrap()
});

/// URL 樣式（改寫自舊版 Constants.RegexUrl；容許無 scheme 的裸網域）。
static URL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?i)(https?://|ftps?://)?",
        r"(([0-9a-z_!~*'().&=+$%\-]+: )?[0-9a-z_!~*'().&=+$%\-]+@)?",
        r"(([0-9]{1,3}\.){3}[0-9]{1,3}",
        r"|([0-9a-z_!~*'()\-]+\.)*([0-9a-z][0-9a-z\-]{0,61})?[0-9a-z]\.[a-z][a-z0-9\-]{1,62})",
        r"(:[0-9]{1,4})?",
        r"(/[0-9a-z_!~*'().;?:@&=+$,%#\-]+)*/?",
    ))
    .unwrap()
});

/// 字元大類（走訪期間用）。
#[derive(Clone, Copy, PartialEq, Eq)]
enum CharClass {
    /// 中文漢字 / 注音 / 〇 / 日文假名（舊版 RegexCut 的可切集合）。
    Han,
    /// ASCII 英數與 &/_（舊版 RegexEngNum 集合）。
    Alnum,
    Space,
    Punct,
    Other,
}

fn char_class(c: char) -> CharClass {
    match c {
        '\u{4E00}'..='\u{9FFF}'          // CJK 統一漢字
        | '\u{3105}'..='\u{3129}'        // 注音符號
        | '\u{3007}'                     // 〇
        | '\u{3040}'..='\u{30FF}'        // 日文平假名/片假名
        | '\u{31F0}'..='\u{31FF}' => CharClass::Han,
        'a'..='z' | 'A'..='Z' | '0'..='9' | '&' | '/' | '_' => CharClass::Alnum,
        c if c.is_whitespace() => CharClass::Space,
        // 常用標點：ASCII 標點（扣除已列入 Alnum 者）+ CJK 標點 + 全形符號 + 一般標點區。
        '!'..='~' => CharClass::Punct, // 剩餘 ASCII 可見字元皆視為標點
        '\u{3000}'..='\u{303F}'          // CJK 符號與標點（。、「」…）
        | '\u{FF00}'..='\u{FFEF}'        // 全形形式（！？，：…）
        | '\u{2000}'..='\u{206F}' => CharClass::Punct,
        _ => CharClass::Other,
    }
}

/// 是否屬於目前分詞管線視為 Han 的字元。
///
/// 除了預切塊本身，主管線也用它辨識跨 ASCII/Han 的詞典詞。
pub(crate) fn is_han_char(c: char) -> bool {
    char_class(c) == CharClass::Han
}

/// 時間詞後綴單位（舊版 RegexTime 的 [年月日號]）。
fn is_time_unit(c: char) -> bool {
    matches!(c, '年' | '月' | '日' | '號')
}

/// 對整段文字做預切塊。輸入須為正規化後文字；`out` 依序收到不重疊、
/// 覆蓋全文的塊。
pub fn split(text: &str, out: &mut Vec<Chunk>) {
    // 快速路徑：無 ASCII 英數者不可能有 URL/email，跳過 regex 掃描。
    let has_ascii = text.bytes().any(|b| b.is_ascii_alphanumeric());

    // 受保護區間：email 優先於 URL（URL 樣式會吃掉 email 的網域部分）。
    let mut protected: Vec<(usize, usize, ChunkKind)> = Vec::new();
    if has_ascii {
        if text.bytes().any(|b| b == b'@') {
            for m in EMAIL_RE.find_iter(text) {
                protected.push((m.start(), m.end(), ChunkKind::Email));
            }
        }
        if text.bytes().any(|b| b == b'.') {
            for m in URL_RE.find_iter(text) {
                // 與 email 重疊者略過；長度 < 4 的裸匹配（如 "a.b"）不視為網址。
                let overlaps = protected
                    .iter()
                    .any(|&(s, e, _)| m.start() < e && s < m.end());
                if !overlaps && m.end() - m.start() >= 4 {
                    protected.push((m.start(), m.end(), ChunkKind::Url));
                }
            }
            protected.sort_by_key(|&(s, _, _)| s);
        }
    }

    // 依受保護區間切出空隙，空隙內做字元類別走訪。
    let mut cursor = 0usize;
    for &(s, e, kind) in &protected {
        if cursor < s {
            scan_plain(&text[cursor..s], cursor, out);
        }
        out.push(Chunk {
            byte_start: s,
            byte_end: e,
            kind,
        });
        cursor = e;
    }
    if cursor < text.len() {
        scan_plain(&text[cursor..], cursor, out);
    }
}

/// 無 URL/email 的純文字走訪：依字元類別分段。
fn scan_plain(text: &str, base: usize, out: &mut Vec<Chunk>) {
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let n = chars.len();
    // 第 i 個字元的結束 byte 位置。
    let end_of = |i: usize| {
        if i + 1 < n {
            chars[i + 1].0
        } else {
            text.len()
        }
    };

    let mut i = 0usize;
    while i < n {
        let (start_byte, c) = chars[i];
        let class = char_class(c);
        match class {
            CharClass::Alnum => {
                // 吃掉整段英數串。
                let mut j = i;
                let mut all_digits = true;
                while j < n && char_class(chars[j].1) == CharClass::Alnum {
                    all_digits &= chars[j].1.is_ascii_digit();
                    j += 1;
                }
                let mut kind = ChunkKind::Eng;
                if all_digits {
                    // 小數/千分位：數字 [./,] 數字 反覆延伸（如 3.14、1,000）。
                    while j < n
                        && matches!(chars[j].1, '.' | ',')
                        && j + 1 < n
                        && chars[j + 1].1.is_ascii_digit()
                    {
                        j += 1;
                        while j < n && chars[j].1.is_ascii_digit() {
                            j += 1;
                        }
                    }
                    kind = ChunkKind::Num;
                    // 時間詞：數字後接 [個]?[年月日號]（如 2014年、3個月）。
                    if j < n && is_time_unit(chars[j].1) {
                        j += 1;
                        kind = ChunkKind::Time;
                    } else if j + 1 < n && chars[j].1 == '個' && is_time_unit(chars[j + 1].1) {
                        j += 2;
                        kind = ChunkKind::Time;
                    }
                }
                out.push(Chunk {
                    byte_start: base + start_byte,
                    byte_end: base + end_of(j - 1),
                    kind,
                });
                i = j;
            }
            CharClass::Han => {
                let mut j = i;
                while j < n && char_class(chars[j].1) == CharClass::Han {
                    j += 1;
                }
                out.push(Chunk {
                    byte_start: base + start_byte,
                    byte_end: base + end_of(j - 1),
                    kind: ChunkKind::Han,
                });
                i = j;
            }
            CharClass::Space | CharClass::Other => {
                // 空白與其他符號：同類連續合併為一段。
                let mut j = i;
                while j < n && char_class(chars[j].1) == class {
                    j += 1;
                }
                let kind = if class == CharClass::Space {
                    ChunkKind::Space
                } else {
                    ChunkKind::Other
                };
                out.push(Chunk {
                    byte_start: base + start_byte,
                    byte_end: base + end_of(j - 1),
                    kind,
                });
                i = j;
            }
            CharClass::Punct => {
                // 標點：僅相同字元的連續（如 「！！！」「……」）合併。
                let mut j = i;
                while j < n && chars[j].1 == c {
                    j += 1;
                }
                out.push(Chunk {
                    byte_start: base + start_byte,
                    byte_end: base + end_of(j - 1),
                    kind: ChunkKind::Punct,
                });
                i = j;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kinds_and_texts(text: &str) -> Vec<(ChunkKind, String)> {
        let mut chunks = Vec::new();
        split(text, &mut chunks);
        chunks
            .iter()
            .map(|c| (c.kind, text[c.byte_start..c.byte_end].to_string()))
            .collect()
    }

    #[test]
    fn extracts_url_and_email() {
        let r = kinds_and_texts("請寄到test@example.com或上https://www.ptt.cc/bbs查詢");
        assert!(
            r.contains(&(ChunkKind::Email, "test@example.com".into())),
            "{r:?}"
        );
        assert!(
            r.contains(&(ChunkKind::Url, "https://www.ptt.cc/bbs".into())),
            "{r:?}"
        );
    }

    #[test]
    fn supports_long_modern_tlds() {
        let r = kinds_and_texts("寄到a@example.technology或看https://example.technology/path");
        assert!(
            r.contains(&(ChunkKind::Email, "a@example.technology".into())),
            "{r:?}"
        );
        assert!(
            r.contains(&(ChunkKind::Url, "https://example.technology/path".into())),
            "{r:?}"
        );
    }

    #[test]
    fn digits_with_time_unit_become_time() {
        let r = kinds_and_texts("2014年開始的3個月內漲了1,000點又3.5%");
        assert!(r.contains(&(ChunkKind::Time, "2014年".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Time, "3個月".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Num, "1,000".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Num, "3.5".into())), "{r:?}");
    }

    #[test]
    fn han_runs_stay_whole_and_cover_all() {
        let text = "台北的天氣真好！！！hello world";
        let mut chunks = Vec::new();
        split(text, &mut chunks);
        // 全覆蓋且不重疊。
        let mut cursor = 0;
        for c in &chunks {
            assert_eq!(c.byte_start, cursor);
            cursor = c.byte_end;
        }
        assert_eq!(cursor, text.len());
        let r = kinds_and_texts(text);
        assert!(
            r.contains(&(ChunkKind::Han, "台北的天氣真好".into())),
            "{r:?}"
        );
        assert!(r.contains(&(ChunkKind::Punct, "！！！".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Eng, "hello".into())), "{r:?}");
    }

    #[test]
    fn chinese_numerals_stay_in_han_chunk() {
        // 中文數字不強制成段，交給詞典（「一起」不能被當數字切走）。
        let r = kinds_and_texts("我們一起走");
        assert_eq!(r, vec![(ChunkKind::Han, "我們一起走".to_string())]);
    }
}
