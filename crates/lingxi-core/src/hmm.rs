//! 未登入詞切分：二階 BMES HMM 的 Viterbi 解碼。
//!
//! 舊版 C# ViterbiCut2 是帶硬編碼特例的樹狀近似搜尋；本實作為數學上
//! 等價目標的標準二階 Viterbi：複合狀態 (prev, cur) 共 16 態的 DP，
//! 首字以 r_emit（P(state|char) 先驗）取代 start 機率（無統計字元已在
//! 轉換階段填回 start 值），並強制首字 ∈ {B,S}、末字 ∈ {E,S}。

use crate::model::{BmesModel, MIN_LOG, STATE_B, STATE_E, STATE_S};
use crate::segment::{SegKind, Segment};

/// 對一段連續 OOV 文字（已正規化）跑二階 Viterbi 重切，
/// 結果以原始輸入座標（byte_base 起算）push 進 `out`，kind 一律 Oov。
pub fn viterbi_cut(m: &BmesModel, run: &str, byte_base: usize, out: &mut Vec<Segment>) {
    let chars: Vec<(usize, char)> = run.char_indices().collect();
    let n = chars.len();
    if n == 0 {
        return;
    }
    let push = |out: &mut Vec<Segment>, a: usize, b: usize| {
        // 輸出字元區間 [a, b]（inclusive）對應的 byte 區間。
        let end = if b + 1 < n { chars[b + 1].0 } else { run.len() };
        out.push(Segment {
            byte_start: byte_base + chars[a].0,
            byte_end: byte_base + end,
            kind: SegKind::Oov,
        });
    };
    if n == 1 {
        push(out, 0, 0);
        return;
    }

    // 發射列查詢：字元不在模型表中時退化為全 MIN_LOG（等同舊版 GetValueOrDefault）。
    let emit1 = |i: usize| -> [f32; 4] {
        m.chars.index_of(chars[i].1).map(|x| m.emit1[x]).unwrap_or([MIN_LOG; 4])
    };
    let emit2 = |i: usize| -> [[f32; 4]; 4] {
        m.chars.index_of(chars[i].1).map(|x| m.emit2[x]).unwrap_or([[MIN_LOG; 4]; 4])
    };
    let r_emit0 = m.chars.index_of(chars[0].1).map(|x| m.r_emit[x]).unwrap_or(m.start);

    // 位置 1 的複合狀態分數：score[(s0, s1)] = 首字先驗 + 一階轉移 + 二階發射。
    // 首字僅允許 B / S。
    const NEG: f32 = f32::NEG_INFINITY;
    let mut score = [NEG; 16];
    let e1_0 = emit1(0);
    let e2_1 = emit2(1);
    for s0 in [STATE_B, STATE_S] {
        let head = r_emit0[s0] + e1_0[s0];
        for s1 in 0..4 {
            score[s0 * 4 + s1] = head + m.trans1[s0][s1] + e2_1[s0][s1];
        }
    }

    // 位置 2..n：以三元轉移遞推，記 backpointer（前前狀態）。
    let mut bps: Vec<[u8; 16]> = Vec::with_capacity(n.saturating_sub(2));
    for i in 2..n {
        let e2_i = emit2(i);
        let mut next = [NEG; 16];
        let mut bp = [0u8; 16];
        for s1 in 0..4 {
            for (s2, &emission) in e2_i[s1].iter().enumerate() {
                let mut best = NEG;
                let mut best_s0 = 0u8;
                for s0 in 0..4 {
                    let v = score[s0 * 4 + s1] + m.trans2[s0][s1][s2];
                    if v > best {
                        best = v;
                        best_s0 = s0 as u8;
                    }
                }
                let idx = s1 * 4 + s2;
                next[idx] = best + emission;
                bp[idx] = best_s0;
            }
        }
        score = next;
        bps.push(bp);
    }

    // 終點限制：末字狀態 ∈ {E, S}。
    let mut best_pair = STATE_B * 4 + STATE_E; // 全 -inf 時的合理預設（BE = 整段一詞）
    let mut best_score = NEG;
    for s1 in 0..4 {
        for s2 in [STATE_E, STATE_S] {
            let idx = s1 * 4 + s2;
            if score[idx] > best_score {
                best_score = score[idx];
                best_pair = idx;
            }
        }
    }

    // 回溯狀態序列。
    let mut states = vec![0u8; n];
    states[n - 1] = (best_pair % 4) as u8;
    states[n - 2] = (best_pair / 4) as u8;
    let mut pair = best_pair;
    for i in (2..n).rev() {
        let s0 = bps[i - 2][pair];
        states[i - 2] = s0;
        pair = (s0 as usize) * 4 + pair / 4;
    }

    // BMES 序列 → 詞段。
    let mut word_start = 0usize;
    for (i, &s) in states.iter().enumerate() {
        match s as usize {
            STATE_B => word_start = i,
            STATE_E => {
                push(out, word_start, i);
                word_start = i + 1;
            }
            STATE_S => {
                push(out, i, i);
                word_start = i + 1;
            }
            _ => {} // M：詞中，續行
        }
    }
    if word_start < n {
        // 理論上終點限制已排除，防禦性收尾避免字元遺失。
        push(out, word_start, n - 1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{BmesModel, CharTable, MIN_LOG};

    /// 手工打造的迷你模型：
    /// 「甲乙」設計成強烈的 B→E（合為一詞），「丙丁」強烈 S,S（各自單字）。
    fn tiny_model() -> BmesModel {
        // CharTable 以 char 排序供二分搜尋（丁 U+4E01 < 丙 U+4E19 < 乙 U+4E59 < 甲 U+7532）。
        let chars = CharTable { chars: vec!['丁', '丙', '乙', '甲'] };
        let uniform_start = [-1.0, MIN_LOG, MIN_LOG, -1.0]; // B 與 S 等權
        // 一階轉移：合理的 BMES 拓撲。
        let mut trans1 = [[MIN_LOG; 4]; 4];
        trans1[0][1] = -2.0; // B→M
        trans1[0][2] = -0.5; // B→E
        trans1[1][1] = -1.0; // M→M
        trans1[1][2] = -0.5; // M→E
        trans1[2][0] = -0.7; // E→B
        trans1[2][3] = -0.7; // E→S
        trans1[3][0] = -0.7; // S→B
        trans1[3][3] = -0.7; // S→S
        // 二階轉移：與一階同構（僅取決於 prev1→cur）。
        let mut trans2 = [[[MIN_LOG; 4]; 4]; 4];
        trans2.fill(trans1);
        let n_chars = chars.chars.len();
        // 發射：甲偏 B、乙偏 E；丙丁偏 S。
        let mut emit1 = vec![[-3.0f32; 4]; n_chars];
        let mut emit2 = vec![[[-3.0f32; 4]; 4]; n_chars];
        let idx = |c: char| chars.index_of(c).unwrap();
        emit1[idx('甲')][0] = -0.1; // 甲 as B
        emit1[idx('乙')][2] = -0.1; // 乙 as E
        emit1[idx('丙')][3] = -0.1; // 丙 as S
        emit1[idx('丁')][3] = -0.1; // 丁 as S
        let pc = idx('乙');
        emit2[pc][0][2] = -0.1; // prev=B, cur=E 時發射乙
        for pc in [idx('丁'), idx('丙')] {
            emit2[pc][3][3] = -0.1; // prev=S, cur=S
            emit2[pc][2][3] = -0.1; // prev=E, cur=S
        }
        // 首字先驗：甲強烈 B、丙丁強烈 S。
        let mut r_emit = vec![uniform_start; n_chars];
        r_emit[idx('甲')] = [-0.1, MIN_LOG, MIN_LOG, -5.0];
        r_emit[idx('丙')] = [-5.0, MIN_LOG, MIN_LOG, -0.1];
        BmesModel { chars, start: uniform_start, trans1, trans2, emit1, emit2, r_emit }
    }

    fn cut_words(m: &BmesModel, text: &str) -> Vec<String> {
        let mut out = Vec::new();
        viterbi_cut(m, text, 0, &mut out);
        out.iter().map(|s| text[s.byte_start..s.byte_end].to_string()).collect()
    }

    #[test]
    fn merges_be_pair_into_word() {
        let m = tiny_model();
        assert_eq!(cut_words(&m, "甲乙"), vec!["甲乙"]);
    }

    #[test]
    fn splits_ss_pair_into_singles() {
        let m = tiny_model();
        assert_eq!(cut_words(&m, "丙丁"), vec!["丙", "丁"]);
    }

    #[test]
    fn longer_mixed_sequence_covers_all_chars() {
        let m = tiny_model();
        let words = cut_words(&m, "甲乙丙丁");
        assert_eq!(words.concat(), "甲乙丙丁");
        assert_eq!(words, vec!["甲乙", "丙", "丁"]);
    }

    #[test]
    fn unknown_chars_still_produce_full_coverage() {
        let m = tiny_model();
        let words = cut_words(&m, "戊己庚");
        assert_eq!(words.concat(), "戊己庚");
    }
}
