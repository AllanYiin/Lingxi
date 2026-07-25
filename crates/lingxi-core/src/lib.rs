//! lingxi-core：繁體中文分詞引擎核心。
//!
//! 管線：預切塊 → 詞典 DAG+DP → 二階 BMES HMM（未登入詞）→ POS Viterbi（OOV 詞性）。
//! 本 crate 只含演算法與模型載入，平行化與 I/O 由上層（CLI / bindings）負責。

pub mod chunk;
pub mod dag;
pub mod dict;
pub mod hmm;
pub mod keyword;
pub mod model;
pub mod pos;
pub mod segment;
pub mod userdict;

use std::path::Path;

use chunk::ChunkKind;
use dict::Dict;
pub use keyword::Keyword;
pub use segment::{SegKind, Segment};
pub use userdict::{parse_user_dict, UserDictEntry};

/// 帶詞性的分詞結果：原始輸入的 byte 區間 + 統一詞性表的 tag id。
/// 詞字串由呼叫端以 `&text[byte_start..byte_end]` 取得（零拷貝），
/// 詞性名稱以 `Segmenter::tag_name(tag)` 解析。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Token {
    pub byte_start: usize,
    pub byte_end: usize,
    pub tag: u8,
}

/// 非中文詞段的內建詞性 id（統一詞性表索引）。
struct BuiltinTags {
    url: u8,
    email: u8,
    eng: u8,
    num: u8,   // "m"
    time: u8,  // "t"
    punct: u8, // "w"
    other: u8, // "x"（空白與其他符號共用）
    unknown: u8,
}

/// 分詞器：載入一次、多執行緒共享（`Send + Sync`，內部無可變狀態）。
pub struct Segmenter {
    dict: Dict,
    bmes: model::BmesModel,
    pos: model::PosModel,
    /// 統一詞性名稱表：合併詞典詞性、POS 模型詞性與內建詞性。
    tags: Vec<String>,
    /// 詞典 tag id → 統一 tag id。
    dict_tag_map: Vec<u8>,
    /// POS 模型 tag id → 統一 tag id。
    pos_tag_map: Vec<u8>,
    builtin: BuiltinTags,
}

/// 模型載入錯誤。
#[derive(Debug)]
pub enum LoadError {
    Io(std::io::Error),
    Asset(model::AssetError),
    UserDict(String),
    TooManyTags(usize),
}

impl std::fmt::Display for LoadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LoadError::Io(e) => write!(f, "讀取模型檔失敗: {e}"),
            LoadError::Asset(e) => write!(f, "{e}"),
            LoadError::UserDict(e) => write!(f, "載入自訂詞典失敗: {e}"),
            LoadError::TooManyTags(n) => {
                write!(f, "統一詞性表共有 {n} 種，超過 u8 可表示的 256 種")
            }
        }
    }
}

impl std::error::Error for LoadError {}

/// 讀取並解碼單一資產檔。
fn load_asset<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, LoadError> {
    let bytes = std::fs::read(path).map_err(LoadError::Io)?;
    model::decode_asset(&bytes).map_err(LoadError::Asset)
}

impl Segmenter {
    /// 從資產目錄載入（需含 dict.bin、hmm_bmes.bin、hmm_pos.bin）。
    pub fn from_asset_dir(dir: impl AsRef<Path>) -> Result<Self, LoadError> {
        Self::from_asset_dir_with_user_dict(dir, &[])
    }

    /// 從資產目錄載入並附加自訂詞典（詞條見 `userdict` 模組）。
    pub fn from_asset_dir_with_user_dict(
        dir: impl AsRef<Path>,
        user_entries: &[UserDictEntry],
    ) -> Result<Self, LoadError> {
        let dir = dir.as_ref();
        Self::from_models_with_user_dict(
            load_asset(&dir.join("dict.bin"))?,
            load_asset(&dir.join("hmm_bmes.bin"))?,
            load_asset(&dir.join("hmm_pos.bin"))?,
            user_entries,
        )
    }

    /// 由已解碼的模型組裝（bindings 內嵌模型時的入口）。
    pub fn from_models(
        dict_model: model::DictModel,
        bmes: model::BmesModel,
        pos: model::PosModel,
    ) -> Result<Self, LoadError> {
        Self::from_models_with_user_dict(dict_model, bmes, pos, &[])
    }

    /// 由已解碼的模型組裝並附加自訂詞典。
    /// 自訂詞典必須在此（統一詞性表建表前）載入，其新增詞性才會進表。
    pub fn from_models_with_user_dict(
        dict_model: model::DictModel,
        bmes: model::BmesModel,
        pos: model::PosModel,
        user_entries: &[UserDictEntry],
    ) -> Result<Self, LoadError> {
        let mut dict = Dict::from_model(dict_model);
        dict.install_user_dict(user_entries)
            .map_err(LoadError::UserDict)?;

        // 統一詞性表：字串為對齊介面，重複名稱共用同一 id。
        let mut tags: Vec<String> = Vec::new();
        let dict_tag_map: Vec<u8> = dict
            .tag_names
            .iter()
            .map(|t| intern_tag(t, &mut tags))
            .collect::<Result<_, _>>()?;
        let pos_tag_map: Vec<u8> = pos
            .tag_names
            .iter()
            .map(|t| intern_tag(t, &mut tags))
            .collect::<Result<_, _>>()?;
        let builtin = BuiltinTags {
            url: intern_tag("url", &mut tags)?,
            email: intern_tag("email", &mut tags)?,
            eng: intern_tag("eng", &mut tags)?,
            num: intern_tag("m", &mut tags)?,
            time: intern_tag("t", &mut tags)?,
            punct: intern_tag("w", &mut tags)?,
            other: intern_tag("x", &mut tags)?,
            unknown: intern_tag("unknown", &mut tags)?,
        };

        Ok(Segmenter {
            dict,
            bmes,
            pos,
            tags,
            dict_tag_map,
            pos_tag_map,
            builtin,
        })
    }

    /// 統一詞性表：tag id → 名稱。
    pub fn tag_name(&self, tag: u8) -> &str {
        &self.tags[tag as usize]
    }

    /// 同 tag_name，id 越界回傳 None（FFI 層列舉詞性表用）。
    pub fn try_tag_name(&self, tag: u8) -> Option<&str> {
        self.tags.get(tag as usize).map(String::as_str)
    }

    /// 分詞：回傳借用輸入的詞切片序列（零拷貝）。
    /// 空白、標點等一律保留為獨立詞段，由呼叫端自行過濾。
    pub fn cut<'a>(&self, text: &'a str) -> Vec<&'a str> {
        self.cut_segments(text)
            .into_iter()
            .map(|s| &text[s.byte_start..s.byte_end])
            .collect()
    }

    /// 分詞：回傳帶 byte 區間與種類的詞段。
    pub fn cut_segments(&self, text: &str) -> Vec<Segment> {
        let normalized = self.dict.normalize(text);
        self.cut_normalized(&normalized)
    }

    /// 分詞＋詞性標註。
    pub fn tokenize(&self, text: &str) -> Vec<Token> {
        let normalized = self.dict.normalize(text);
        self.cut_normalized(&normalized)
            .into_iter()
            .map(|s| Token {
                byte_start: s.byte_start,
                byte_end: s.byte_end,
                tag: self.tag_of(&normalized, &s),
            })
            .collect()
    }

    /// 主管線（輸入須已正規化）。
    fn cut_normalized(&self, normalized: &str) -> Vec<Segment> {
        let mut out = Vec::with_capacity(normalized.len() / 4);
        let mut chunks = Vec::new();
        let mut scratch: Vec<Segment> = Vec::new();

        // 一般預切塊會把 ASCII/數字與 Han 分開；先保護真正跨邊界的詞典詞，
        // 讓「AV女優」「90後」與同類 userdict 詞條仍可命中。其餘區段維持
        // 原本 chunk → DAG → HMM 管線，避免把任意英中混合文字送進中文 HMM。
        let anchors = self.mixed_dict_anchors(normalized);
        let mut cursor = 0usize;
        for anchor in anchors {
            self.cut_range(
                normalized,
                cursor,
                anchor.byte_start,
                &mut chunks,
                &mut scratch,
                &mut out,
            );
            out.push(anchor);
            cursor = anchor.byte_end;
        }
        self.cut_range(
            normalized,
            cursor,
            normalized.len(),
            &mut chunks,
            &mut scratch,
            &mut out,
        );
        out
    }

    /// 找出跨 ASCII/Han 邊界的詞典詞，採左至右最長匹配並以機率破同長平手。
    fn mixed_dict_anchors(&self, normalized: &str) -> Vec<Segment> {
        let mut candidates: Vec<(usize, usize, u32, f32)> = self
            .dict
            .matches(normalized)
            .filter_map(|m| {
                let word = &normalized[m.byte_start..m.byte_end];
                let has_ascii = word.chars().any(|c| c.is_ascii_alphanumeric());
                let has_han = word.chars().any(chunk::is_han_char);
                (has_ascii && has_han).then(|| {
                    (
                        m.byte_start,
                        m.byte_end,
                        m.word_id,
                        self.dict.log_prob(m.word_id),
                    )
                })
            })
            .collect();
        candidates.sort_by(|a, b| {
            a.0.cmp(&b.0)
                .then_with(|| b.1.cmp(&a.1))
                .then_with(|| b.3.total_cmp(&a.3))
        });

        let mut out = Vec::new();
        let mut cursor = 0usize;
        for (start, end, word_id, _) in candidates {
            if start < cursor {
                continue;
            }
            out.push(Segment {
                byte_start: start,
                byte_end: end,
                kind: SegKind::Dict(word_id),
            });
            cursor = end;
        }
        out
    }

    /// 對不含混合詞典保護區間的子範圍執行原本主管線。
    fn cut_range(
        &self,
        normalized: &str,
        start: usize,
        end: usize,
        chunks: &mut Vec<chunk::Chunk>,
        scratch: &mut Vec<Segment>,
        out: &mut Vec<Segment>,
    ) {
        if start >= end {
            return;
        }
        chunks.clear();
        chunk::split(&normalized[start..end], chunks);
        for ch in chunks.iter() {
            let byte_start = start + ch.byte_start;
            let byte_end = start + ch.byte_end;
            match ch.kind {
                ChunkKind::Han => {
                    scratch.clear();
                    dag::cut_dag(
                        &self.dict,
                        &normalized[byte_start..byte_end],
                        byte_start,
                        scratch,
                    );
                    self.merge_oov_runs(normalized, scratch, out);
                }
                kind => out.push(Segment {
                    byte_start,
                    byte_end,
                    kind: direct_kind(kind),
                }),
            }
        }
    }

    /// 詞段 → 統一詞性 id。
    fn tag_of(&self, normalized: &str, seg: &Segment) -> u8 {
        match seg.kind {
            SegKind::Dict(id) => self.dict_tag_map[self.dict.tag(id) as usize],
            SegKind::Oov => {
                let word = &normalized[seg.byte_start..seg.byte_end];
                pos::tag_oov(&self.pos, word)
                    .map(|t| self.pos_tag_map[t as usize])
                    .unwrap_or(self.builtin.unknown)
            }
            SegKind::Url => self.builtin.url,
            SegKind::Email => self.builtin.email,
            SegKind::Eng => self.builtin.eng,
            SegKind::Num => self.builtin.num,
            SegKind::Time => self.builtin.time,
            SegKind::Punct => self.builtin.punct,
            SegKind::Space | SegKind::Other => self.builtin.other,
        }
    }

    /// 對 DAG 輸出做 jieba 式未登入詞合併：連續 ≥2 個單字詞段
    /// （不論是否詞典單字）合併丟給二階 HMM 重切，其餘原樣輸出。
    fn merge_oov_runs(&self, normalized: &str, segs: &[Segment], out: &mut Vec<Segment>) {
        let is_single = |s: &Segment| {
            let t = &normalized[s.byte_start..s.byte_end];
            t.chars()
                .next()
                .map(|c| c.len_utf8() == t.len())
                .unwrap_or(false)
        };
        let mut run_start = 0usize; // 進行中單字 run 的起始索引
        let mut run_len = 0usize;
        for (i, seg) in segs.iter().enumerate() {
            if is_single(seg) {
                if run_len == 0 {
                    run_start = i;
                }
                run_len += 1;
            } else {
                self.flush_run(normalized, &segs[run_start..run_start + run_len], out);
                run_len = 0;
                out.push(*seg);
            }
        }
        self.flush_run(normalized, &segs[run_start..run_start + run_len], out);
    }

    /// 收尾一段單字 run：長度 ≥2 走 HMM；恰為詞典整詞時尊重 DP 的逐字選擇。
    fn flush_run(&self, normalized: &str, run: &[Segment], out: &mut Vec<Segment>) {
        match run.len() {
            0 => {}
            1 => out.push(run[0]),
            _ => {
                let a = run[0].byte_start;
                let b = run[run.len() - 1].byte_end;
                let run_str = &normalized[a..b];
                // 整串本身是詞典詞 → DP 刻意選了逐字路徑，不再合併。
                let whole_is_word = self
                    .dict
                    .matches(run_str)
                    .any(|m| m.byte_start == 0 && m.byte_end == run_str.len());
                if whole_is_word {
                    out.extend_from_slice(run);
                } else {
                    hmm::viterbi_cut(&self.bmes, run_str, a, out);
                }
            }
        }
    }
}

fn intern_tag(name: &str, tags: &mut Vec<String>) -> Result<u8, LoadError> {
    if let Some(i) = tags.iter().position(|t| t == name) {
        return Ok(i as u8);
    }
    if tags.len() > u8::MAX as usize {
        return Err(LoadError::TooManyTags(tags.len() + 1));
    }
    tags.push(name.to_string());
    Ok((tags.len() - 1) as u8)
}

/// 預切塊種類 → 詞段種類（Han 不在此列，另走 DAG）。
fn direct_kind(kind: ChunkKind) -> SegKind {
    match kind {
        ChunkKind::Url => SegKind::Url,
        ChunkKind::Email => SegKind::Email,
        ChunkKind::Eng => SegKind::Eng,
        ChunkKind::Num => SegKind::Num,
        ChunkKind::Time => SegKind::Time,
        ChunkKind::Punct => SegKind::Punct,
        ChunkKind::Space => SegKind::Space,
        ChunkKind::Other => SegKind::Other,
        ChunkKind::Han => unreachable!("Han chunk 走 DAG 分支"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unified_tag_table_rejects_more_than_256_names() {
        let mut tags = Vec::new();
        for i in 0..=u8::MAX {
            assert!(matches!(intern_tag(&format!("tag-{i}"), &mut tags), Ok(id) if id == i));
        }
        assert!(matches!(
            intern_tag("overflow", &mut tags),
            Err(LoadError::TooManyTags(257))
        ));
    }
}
