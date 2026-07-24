//! DAG 建構與動態規劃最佳路徑。
//!
//! 對一段（已正規化的）連續文字：AC 掃描收集所有詞典命中為 DAG 邊，
//! 由右至左 DP 取 log 機率總和最大的切分路徑（jieba 式）。
//! 舊版 C# 在 DP 迴圈內做線性 LINQ 掃描導致近 O(n³)；本實作為
//! O(邊數 + 字元數)，邊在單次 AC 掃描中產生。

use crate::dict::Dict;
use crate::segment::{SegKind, Segment};

/// 一條 DAG 邊：從某字元位置起的候選詞。
#[derive(Clone, Copy)]
struct Edge {
    /// 邊終點的字元位置（exclusive）。
    end: u32,
    /// 候選詞的 ln(freq/total)；未登入單字為平滑值。
    log_prob: f32,
    /// 詞條 id；未登入單字邊為 None。
    word_id: Option<u32>,
}

/// 對一段連續文字做 DAG+DP 切分，結果 push 進 `out`。
///
/// `chunk` 為已正規化文字的子切片；`byte_base` 是它在原始輸入中的
/// byte 偏移，輸出的 Segment byte 區間一律以原始輸入為座標系。
pub fn cut_dag(dict: &Dict, chunk: &str, byte_base: usize, out: &mut Vec<Segment>) {
    // 字元邊界表：boundaries[i] = 第 i 個字元的 byte 起點，末尾為 chunk.len()。
    let boundaries: Vec<u32> = chunk
        .char_indices()
        .map(|(b, _)| b as u32)
        .chain(std::iter::once(chunk.len() as u32))
        .collect();
    let n = boundaries.len() - 1; // 字元數
    if n == 0 {
        return;
    }
    // 單字元 chunk 直接輸出，省去建 DAG。
    if n == 1 {
        // 主詞典與自訂詞典皆可能命中同一字，取機率高者（自訂覆蓋語意）。
        let word_id = dict
            .matches(chunk)
            .filter(|m| m.byte_end == chunk.len())
            .max_by(|a, b| dict.log_prob(a.word_id).total_cmp(&dict.log_prob(b.word_id)))
            .map(|m| m.word_id);
        out.push(Segment {
            byte_start: byte_base,
            byte_end: byte_base + chunk.len(),
            kind: word_id.map_or(SegKind::Oov, SegKind::Dict),
        });
        return;
    }

    // 收集 DAG 邊，按起點字元位置分桶。
    let mut edges: Vec<Vec<Edge>> = vec![Vec::new(); n];
    let mut has_single: Vec<bool> = vec![false; n];
    let char_pos_of = |byte: u32| boundaries.binary_search(&byte).expect("命中必落在字元邊界");
    for m in dict.matches(chunk) {
        let start = char_pos_of(m.byte_start as u32);
        let end = char_pos_of(m.byte_end as u32);
        if end - start == 1 {
            has_single[start] = true;
        }
        edges[start].push(Edge {
            end: end as u32,
            log_prob: dict.log_prob(m.word_id),
            word_id: Some(m.word_id),
        });
    }
    // 每個位置補上未登入單字邊（若無詞典單字邊），保證 DAG 連通，
    // 也讓「跳過詞典多字詞、逐字走」的路徑始終存在（與 jieba 語意一致）。
    for i in 0..n {
        if !has_single[i] {
            edges[i].push(Edge {
                end: (i + 1) as u32,
                log_prob: dict.oov_char_log_prob,
                word_id: None,
            });
        }
    }

    // 由右至左 DP：route[i] = 從位置 i 切到句尾的最大 log 機率與最佳邊。
    // f32 累加對長 chunk 精度足夠（log 值域小、chunk 通常短）。
    let mut route: Vec<(f32, u32)> = vec![(0.0, 0); n + 1];
    for i in (0..n).rev() {
        let mut best = (f32::NEG_INFINITY, i as u32 + 1);
        for e in &edges[i] {
            let score = e.log_prob + route[e.end as usize].0;
            if score > best.0 {
                best = (score, e.end);
            }
        }
        route[i] = best;
    }

    // 沿最佳路徑輸出詞段。
    let mut i = 0usize;
    while i < n {
        let end = route[i].1 as usize;
        // 重查該邊的 word_id：邊桶內線性找（每桶通常僅數條）。
        // 同區間可能有主詞典與自訂詞典兩條邊，取機率高者（DP 選中的即是它）。
        let word_id = edges[i]
            .iter()
            .filter(|e| e.end as usize == end)
            .max_by(|a, b| a.log_prob.total_cmp(&b.log_prob))
            .and_then(|e| e.word_id);
        out.push(Segment {
            byte_start: byte_base + boundaries[i] as usize,
            byte_end: byte_base + boundaries[end] as usize,
            kind: word_id.map_or(SegKind::Oov, SegKind::Dict),
        });
        i = end;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dict::Dict;
    use crate::model::DictModel;
    use daachorse::CharwiseDoubleArrayAhoCorasick;

    /// 以小詞典建構 Dict：words = (詞, 頻率, 詞性)。
    fn tiny_dict(words: &[(&str, f64, &str)]) -> Dict {
        let total: f64 = words.iter().map(|(_, f, _)| f).sum();
        let mut sorted: Vec<_> = words.to_vec();
        sorted.sort_by_key(|(w, _, _)| w.to_string());
        let tag_names: Vec<String> = {
            let mut t: Vec<String> = sorted.iter().map(|(_, _, t)| t.to_string()).collect();
            t.sort();
            t.dedup();
            t
        };
        let patterns: Vec<(&str, u32)> =
            sorted.iter().enumerate().map(|(i, (w, _, _))| (*w, i as u32)).collect();
        let automaton: CharwiseDoubleArrayAhoCorasick<u32> =
            CharwiseDoubleArrayAhoCorasick::with_values(patterns).unwrap();
        Dict::from_model(DictModel {
            automaton_bytes: automaton.serialize(),
            word_tags: sorted
                .iter()
                .map(|(_, _, t)| tag_names.iter().position(|x| x == t).unwrap() as u8)
                .collect(),
            word_log_probs: sorted.iter().map(|(_, f, _)| ((f / total).ln()) as f32).collect(),
            word_char_lens: sorted.iter().map(|(w, _, _)| w.chars().count() as u8).collect(),
            tag_names,
            total_log: total.ln() as f32,
            variant_map: vec![],
        })
    }

    fn cut_words<'a>(dict: &Dict, text: &'a str) -> Vec<&'a str> {
        let mut segs = Vec::new();
        cut_dag(dict, text, 0, &mut segs);
        segs.iter().map(|s| &text[s.byte_start..s.byte_end]).collect()
    }

    #[test]
    fn dp_prefers_high_frequency_path() {
        // 「北京天安門」整詞頻率高於「北京」+「天安門」分開時，應切整詞。
        let dict = tiny_dict(&[
            ("北京", 100.0, "ns"),
            ("天安門", 80.0, "ns"),
            ("北京天安門", 5000.0, "ns"),
            ("我", 500.0, "r"),
            ("愛", 300.0, "v"),
        ]);
        assert_eq!(cut_words(&dict, "我愛北京天安門"), vec!["我", "愛", "北京天安門"]);
    }

    #[test]
    fn dp_splits_when_parts_win() {
        // 整詞頻率極低時，DP 應選「北京 / 天安門」。
        // ln(1/5981)+... vs ln(100/5981)+ln(80/5981)：分開較大。
        let dict = tiny_dict(&[
            ("北京", 100.0, "ns"),
            ("天安門", 80.0, "ns"),
            ("北京天安門", 1.0, "ns"),
            ("我", 500.0, "r"),
            ("愛", 300.0, "v"),
        ]);
        assert_eq!(cut_words(&dict, "我愛北京天安門"), vec!["我", "愛", "北京", "天安門"]);
    }

    #[test]
    fn oov_chars_fall_back_to_single() {
        // 詞典完全沒有的字應逐字輸出且 kind 為 Oov。
        let dict = tiny_dict(&[("你好", 10.0, "l")]);
        let mut segs = Vec::new();
        cut_dag(&dict, "你好嗎", 0, &mut segs);
        let words: Vec<&str> = segs.iter().map(|s| &"你好嗎"[s.byte_start..s.byte_end]).collect();
        assert_eq!(words, vec!["你好", "嗎"]);
        assert!(matches!(segs[0].kind, SegKind::Dict(_)));
        assert_eq!(segs[1].kind, SegKind::Oov);
    }

    #[test]
    fn byte_base_offsets_are_applied() {
        let dict = tiny_dict(&[("你好", 10.0, "l")]);
        let mut segs = Vec::new();
        cut_dag(&dict, "你好", 30, &mut segs);
        assert_eq!((segs[0].byte_start, segs[0].byte_end), (30, 36));
    }

    #[test]
    fn user_word_auto_freq_beats_current_split() {
        // 主詞典會把「板南線」切成 板南/線；加入自訂詞（頻率自動推定）後應成一詞。
        let mut dict = tiny_dict(&[("板南", 100.0, "ns"), ("線", 50.0, "n"), ("搭", 20.0, "v")]);
        assert_eq!(cut_words(&dict, "搭板南線"), vec!["搭", "板南", "線"]);
        dict.install_user_dict(&[crate::userdict::UserDictEntry {
            word: "板南線".into(),
            freq: None,
            tag: Some("nt".into()),
        }])
        .unwrap();
        let mut segs = Vec::new();
        cut_dag(&dict, "搭板南線", 0, &mut segs);
        let words: Vec<&str> =
            segs.iter().map(|s| &"搭板南線"[s.byte_start..s.byte_end]).collect();
        assert_eq!(words, vec!["搭", "板南線"]);
        // 新詞性 nt 應已擴充進 tag_names，且該詞段回查得到它。
        let SegKind::Dict(id) = segs[1].kind else { panic!("應為詞典詞") };
        assert_eq!(dict.tag_names[dict.tag(id) as usize], "nt");
    }

    #[test]
    fn user_word_overrides_tag_of_existing_word() {
        // 同一詞主詞典與自訂詞典皆有時，顯式高頻的自訂詞條應贏得詞性回查。
        let mut dict = tiny_dict(&[("雲端", 100.0, "n")]);
        dict.install_user_dict(&[crate::userdict::UserDictEntry {
            word: "雲端".into(),
            freq: Some(10000.0),
            tag: Some("nz".into()),
        }])
        .unwrap();
        let mut segs = Vec::new();
        cut_dag(&dict, "雲端", 0, &mut segs);
        let SegKind::Dict(id) = segs[0].kind else { panic!("應為詞典詞") };
        assert_eq!(dict.tag_names[dict.tag(id) as usize], "nz");
    }

    #[test]
    fn user_dict_normalizes_words_like_queries() {
        // 自訂詞含 ASCII 大寫時應與查詢端同樣正規化（小寫）後才建自動機。
        let mut dict = tiny_dict(&[("好", 10.0, "a")]);
        dict.install_user_dict(&[crate::userdict::UserDictEntry {
            word: "GPT模型".into(),
            freq: Some(100.0),
            tag: None,
        }])
        .unwrap();
        assert_eq!(cut_words(&dict, "gpt模型好"), vec!["gpt模型", "好"]);
    }
}
