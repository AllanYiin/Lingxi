//! DAG 建構與動態規劃最佳路徑。
//!
//! 對一段（已正規化的）連續文字：AC 掃描收集所有詞典命中為 DAG 邊，
//! 由右至左 DP 取 log 機率總和最大的切分路徑（jieba 式）。
//! 舊版 C# 在 DP 迴圈內做線性 LINQ 掃描導致近 O(n³)；本實作為
//! O(邊數 + 字元數)，邊在單次 AC 掃描中產生。

use crate::dict::Dict;

/// 一條 DAG 邊：從某字元位置起、跨 `char_len` 個字元的候選詞。
#[derive(Clone, Copy)]
struct Edge {
    /// 邊終點的字元位置（exclusive）。
    end: u32,
    /// 候選詞的 ln(freq/total)；未登入單字為平滑值。
    log_prob: f32,
    /// 詞條 id；未登入單字邊為 None。
    word_id: Option<u32>,
}

/// 切分結果中的一個詞段：輸入字串的 byte 區間 + 詞條 id（若為詞典詞）。
#[derive(Clone, Copy, Debug)]
pub struct Segment {
    pub byte_start: usize,
    pub byte_end: usize,
    /// Some(id) = 詞典詞；None = 未登入單字（後續交給 HMM 合併重切）。
    pub word_id: Option<u32>,
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
        let word_id = dict.matches(chunk).find(|m| m.byte_end == chunk.len()).map(|m| m.word_id);
        out.push(Segment { byte_start: byte_base, byte_end: byte_base + chunk.len(), word_id });
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
        let word_id = edges[i]
            .iter()
            .find(|e| e.end as usize == end)
            .and_then(|e| e.word_id);
        out.push(Segment {
            byte_start: byte_base + boundaries[i] as usize,
            byte_end: byte_base + boundaries[end] as usize,
            word_id,
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
        // 詞典完全沒有的字應逐字輸出且 word_id = None。
        let dict = tiny_dict(&[("你好", 10.0, "l")]);
        let mut segs = Vec::new();
        cut_dag(&dict, "你好嗎", 0, &mut segs);
        let words: Vec<&str> = segs.iter().map(|s| &"你好嗎"[s.byte_start..s.byte_end]).collect();
        assert_eq!(words, vec!["你好", "嗎"]);
        assert!(segs[0].word_id.is_some());
        assert!(segs[1].word_id.is_none());
    }

    #[test]
    fn byte_base_offsets_are_applied() {
        let dict = tiny_dict(&[("你好", 10.0, "l")]);
        let mut segs = Vec::new();
        cut_dag(&dict, "你好", 30, &mut segs);
        assert_eq!((segs[0].byte_start, segs[0].byte_end), (30, 36));
    }
}
