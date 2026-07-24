//! OOV 詞的詞性標註：joint-state（BMES × 詞性）HMM Viterbi。
//!
//! 只有未登入詞才走此模型；詞典詞的詞性直接查詞典（因此舊版 16.5MB 的
//! word_state_tag 資產完全不需要）。一個 OOV 詞的 BMES 型態固定為
//! B M... M E（單字為 S），據此在每個位置過濾候選狀態，最終詞性取
//! 最佳路徑末字狀態的 tag 部分。

use crate::model::{PosModel, MIN_LOG, STATE_B, STATE_E, STATE_M, STATE_S};

/// 字元不在模型、但狀態集允許時的發射地板值（與轉換工具的 EMIT_FLOOR 一致）。
const EMIT_FLOOR: f32 = -50.0;

/// 對一個 OOV 詞（已正規化）推斷詞性，回傳 PosModel.tag_names 的索引。
/// 模型完全無資訊（所有字元都不在表中）時回傳 None，由呼叫端給預設詞性。
pub fn tag_oov(m: &PosModel, word: &str) -> Option<u8> {
    let chars: Vec<char> = word.chars().collect();
    let n = chars.len();
    if n == 0 {
        return None;
    }

    // 位置 i 應有的 BMES 型態。
    let bmes_at = |i: usize| -> usize {
        if n == 1 {
            STATE_S
        } else if i == 0 {
            STATE_B
        } else if i == n - 1 {
            STATE_E
        } else {
            STATE_M
        }
    };

    // 取位置 i 的候選 (state, emit)：模型有此字元 → 過濾 CSR 列；
    // 沒有 → 全部符合 BMES 型態的狀態，發射用地板值。
    let candidates = |i: usize| -> Vec<(u16, f32)> {
        let want = bmes_at(i) as u8;
        match m.emit_row(chars[i]) {
            Some((states, logps)) => states
                .iter()
                .zip(logps)
                .filter(|(&s, _)| m.state_bmes[s as usize] == want)
                .map(|(&s, &p)| (s, p))
                .collect(),
            None => (0..m.state_names.len() as u16)
                .filter(|&s| m.state_bmes[s as usize] == want)
                .map(|s| (s, EMIT_FLOOR))
                .collect(),
        }
    };

    let s_count = m.state_names.len();
    let first = candidates(0);
    if first.is_empty() {
        return None;
    }
    // score[state] 只在候選內有值；用 (score, backpointer) 的 dense 向量換取簡單索引。
    let mut score = vec![f32::NEG_INFINITY; s_count];
    for &(s, e) in &first {
        score[s as usize] = m.start[s as usize].max(MIN_LOG) + e;
    }
    let mut prev_cands = first;

    // 只需要末字的最佳狀態（詞性 = 其 tag 部分），不必記 backpointer 回溯。
    for i in 1..n {
        let cands = candidates(i);
        if cands.is_empty() {
            return None;
        }
        let mut next_score = vec![f32::NEG_INFINITY; s_count];
        for &(s, e) in &cands {
            let mut best = f32::NEG_INFINITY;
            for &(p, _) in &prev_cands {
                let v = score[p as usize] + m.trans[p as usize * s_count + s as usize];
                if v > best {
                    best = v;
                }
            }
            next_score[s as usize] = best + e;
        }
        score = next_score;
        prev_cands = cands;
    }

    let (best_state, _) = prev_cands
        .iter()
        .map(|&(s, _)| (s, score[s as usize]))
        .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))?;
    Some(m.state_tags[best_state as usize])
}
