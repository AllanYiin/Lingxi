//! 詞典：AC 自動機查詢 + 文字正規化。
//!
//! `Dict` 由 `DictModel` 資產建構，提供一次掃描取得句中所有詞典命中
//! （即 DAG 的全部邊），以及與轉換階段完全一致的文字正規化。

use std::borrow::Cow;

use daachorse::CharwiseDoubleArrayAhoCorasick;

use crate::model::DictModel;

/// 執行期詞典。所有查詢皆為唯讀，`Send + Sync`。
pub struct Dict {
    automaton: CharwiseDoubleArrayAhoCorasick<u32>,
    /// 詞性名稱表；詞條 tag id 索引此表。
    pub tag_names: Vec<String>,
    word_tags: Vec<u8>,
    word_log_probs: Vec<f32>,
    /// 未登入單字的平滑 log 機率 = ln(0.5) - ln(total_freq)。
    pub oov_char_log_prob: f32,
    /// 異體字映射（皆為 UTF-8 等長對）。
    variant_map: Vec<(char, char)>,
}

/// 一筆詞典命中：位於輸入字串的 byte 區間與詞條 id。
#[derive(Clone, Copy, Debug)]
pub struct DictMatch {
    pub byte_start: usize,
    pub byte_end: usize,
    pub word_id: u32,
}

impl Dict {
    /// 由資產模型建構。
    ///
    /// automaton bytes 來自 lingxi-convert 的 daachorse serialize；
    /// 資產檔頭已做 xxh3 校驗，故此處信任其內容（deserialize_unchecked）。
    pub fn from_model(m: DictModel) -> Self {
        let (automaton, _rest) = unsafe {
            CharwiseDoubleArrayAhoCorasick::<u32>::deserialize_unchecked(&m.automaton_bytes)
        };
        Dict {
            automaton,
            tag_names: m.tag_names,
            word_tags: m.word_tags,
            word_log_probs: m.word_log_probs,
            oov_char_log_prob: 0.5f32.ln() - m.total_log,
            variant_map: m.variant_map,
        }
    }

    /// 一次掃描回傳文字（須已正規化）中的所有詞典命中，byte 區間可重疊。
    #[inline]
    pub fn matches<'s>(&'s self, normalized: &'s str) -> impl Iterator<Item = DictMatch> + 's {
        self.automaton.find_overlapping_iter(normalized).map(|m| DictMatch {
            byte_start: m.start(),
            byte_end: m.end(),
            word_id: m.value(),
        })
    }

    /// 詞條的 ln(freq/total)。
    #[inline]
    pub fn log_prob(&self, word_id: u32) -> f32 {
        self.word_log_probs[word_id as usize]
    }

    /// 詞條的詞性 id（索引 `tag_names`）。
    #[inline]
    pub fn tag(&self, word_id: u32) -> u8 {
        self.word_tags[word_id as usize]
    }

    /// 文字正規化：ASCII 小寫 + 異體字替換。
    ///
    /// 與 lingxi-convert 對詞典 key 的正規化完全一致，且保證輸出與輸入
    /// byte 長度相同（ASCII 小寫等長、異體字對已驗證等長），因此在
    /// 正規化字串上得到的 byte offset 可直接切片原始輸入。
    /// 無須修改時回傳 Borrowed，零配置。
    pub fn normalize<'a>(&self, text: &'a str) -> Cow<'a, str> {
        let needs_change = text
            .chars()
            .any(|c| c.is_ascii_uppercase() || self.variant_map.iter().any(|(f, _)| *f == c));
        if !needs_change {
            return Cow::Borrowed(text);
        }
        Cow::Owned(
            text.chars()
                .map(|c| {
                    let c = c.to_ascii_lowercase();
                    self.variant_map
                        .iter()
                        .find(|(f, _)| *f == c)
                        .map(|(_, t)| *t)
                        .unwrap_or(c)
                })
                .collect(),
        )
    }
}
