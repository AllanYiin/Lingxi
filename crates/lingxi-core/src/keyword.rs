//! TextRank 關鍵字抽取（jieba 相容參數：window=5、d=0.85、10 次迭代）。
//!
//! 流程：分詞＋詞性 → 依詞性與長度篩候選 → 滑動視窗共現建無向加權圖 →
//! PageRank（Gauss-Seidel 式就地更新）→ min-max 正規化 → 取 top-k。
//! 純演算法、無額外模型資產；IDF 類統計方案（TF-IDF）另案處理。

use std::collections::HashMap;

use crate::Segmenter;

/// 一個關鍵字：詞（正規化後表面形）與正規化到 [0,1] 附近的權重。
#[derive(Clone, Debug, PartialEq)]
pub struct Keyword {
    pub word: String,
    pub weight: f32,
}

/// 共現視窗大小：候選詞與其後 SPAN-1 個詞內的候選詞連邊（與 jieba 相同）。
const SPAN: usize = 5;
/// PageRank 阻尼係數。
const DAMPING: f32 = 0.85;
/// PageRank 迭代次數。
const ITERATIONS: usize = 10;

/// 預設候選詞性：名詞類（n 開頭：n/nr/ns/nt/nz…）、動詞 v/vn、英文詞。
/// 文件關鍵字場景比 jieba 預設（ns/n/vn/v）多收人名機構名與英文術語。
fn default_allow(tag: &str) -> bool {
    tag.starts_with('n') || tag == "v" || tag == "vn" || tag == "eng"
}

impl Segmenter {
    /// TextRank 關鍵字抽取，預設詞性過濾（見 `default_allow`）。
    pub fn extract_keywords(&self, text: &str, top_k: usize) -> Vec<Keyword> {
        self.extract_keywords_with(text, top_k, None)
    }

    /// TextRank 關鍵字抽取；`allow_tags` 指定候選詞性白名單（None = 預設）。
    pub fn extract_keywords_with(
        &self,
        text: &str,
        top_k: usize,
        allow_tags: Option<&[&str]>,
    ) -> Vec<Keyword> {
        // 在正規化文字上取詞，使節點識別與詞典一致（ASCII 小寫、異體字統一），
        // 「AI」與「ai」才會聚合成同一節點。
        let normalized = self.dict.normalize(text);
        let tokens = self.tokenize(&normalized);
        let allowed = |tag: &str| match allow_tags {
            Some(list) => list.contains(&tag),
            None => default_allow(tag),
        };

        // 候選詞 → 節點 id；每個 token 記錄其節點 id（非候選為 None）。
        let mut node_of: HashMap<&str, usize> = HashMap::new();
        let mut words: Vec<&str> = Vec::new();
        let cand: Vec<Option<usize>> = tokens
            .iter()
            .map(|t| {
                let w = &normalized[t.byte_start..t.byte_end];
                if w.chars().count() < 2 || !allowed(self.tag_name(t.tag)) {
                    return None;
                }
                Some(*node_of.entry(w).or_insert_with(|| {
                    words.push(w);
                    words.len() - 1
                }))
            })
            .collect();
        let n = words.len();
        if n == 0 {
            return Vec::new();
        }

        // 共現計數 → 無向加權鄰接表（雙向各存一份；自環亦存兩份，與 jieba 一致）。
        let mut cooc: HashMap<(usize, usize), f32> = HashMap::new();
        for i in 0..cand.len() {
            let Some(a) = cand[i] else { continue };
            for &candidate in cand.iter().take((i + SPAN).min(cand.len())).skip(i + 1) {
                let Some(b) = candidate else { continue };
                *cooc.entry((a, b)).or_default() += 1.0;
            }
        }
        let mut adj: Vec<Vec<(usize, f32)>> = vec![Vec::new(); n];
        for (&(a, b), &w) in &cooc {
            adj[a].push((b, w));
            adj[b].push((a, w));
        }
        let out_sum: Vec<f32> = adj.iter().map(|es| es.iter().map(|(_, w)| w).sum()).collect();

        // PageRank：就地更新（同一輪內後算的節點看到前面節點的新值）。
        let mut ws = vec![1.0 / n as f32; n];
        for _ in 0..ITERATIONS {
            for u in 0..n {
                let s: f32 = adj[u]
                    .iter()
                    .filter(|&&(v, _)| out_sum[v] > 0.0)
                    .map(|&(v, w)| w / out_sum[v] * ws[v])
                    .sum();
                ws[u] = (1.0 - DAMPING) + DAMPING * s;
            }
        }

        // min-max 正規化（jieba 同款：min 除以 10 保留底部區分度）。
        let (mut lo, mut hi) = (f32::INFINITY, f32::NEG_INFINITY);
        for &w in &ws {
            lo = lo.min(w);
            hi = hi.max(w);
        }
        let denom = hi - lo / 10.0;
        if denom > 0.0 {
            for w in &mut ws {
                *w = (*w - lo / 10.0) / denom;
            }
        }

        // 權重降冪、同分依詞排序（輸出穩定），取 top-k。
        let mut order: Vec<usize> = (0..n).collect();
        order.sort_by(|&a, &b| ws[b].total_cmp(&ws[a]).then_with(|| words[a].cmp(words[b])));
        order
            .into_iter()
            .take(top_k)
            .map(|i| Keyword { word: words[i].to_string(), weight: ws[i] })
            .collect()
    }
}
