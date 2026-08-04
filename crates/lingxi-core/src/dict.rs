//! 詞典：AC 自動機查詢 + 文字正規化。
//!
//! `Dict` 由 `DictModel` 資產建構，提供一次掃描取得句中所有詞典命中
//! （即 DAG 的全部邊），以及與轉換階段完全一致的文字正規化。

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use daachorse::CharwiseDoubleArrayAhoCorasick;

use crate::model::DictModel;
use crate::userdict::UserDictEntry;

/// 執行期詞典。所有查詢皆為唯讀，`Send + Sync`。
/// 自訂詞典（若有）於建構期一次載入，之後與主詞典同樣不可變。
pub struct Dict {
    automaton: CharwiseDoubleArrayAhoCorasick<u32>,
    /// 詞性名稱表；詞條 tag id 索引此表。含自訂詞條新增的詞性。
    pub tag_names: Vec<String>,
    word_tags: Vec<u8>,
    word_log_probs: Vec<f32>,
    /// 從資產 logp 還原的主詞典正頻率，供 runtime 覆寫後重新正規化。
    word_freqs: Vec<f64>,
    /// 被 runtime 詞條覆寫的主詞典 id。
    overridden_main_ids: HashSet<u32>,
    /// 異體字映射（皆為 UTF-8 等長對）。
    variant_map: Vec<(char, char)>,
    /// 自訂詞典自動機；詞條 id 自 `word_tags.len()` 起編，與主詞典共用 id 空間。
    user: Option<UserDict>,
}

/// 自訂詞典的執行期結構（平行陣列語意同主詞典）。
struct UserDict {
    automaton: CharwiseDoubleArrayAhoCorasick<u32>,
    tags: Vec<u8>,
    log_probs: Vec<f32>,
    id_base: u32,
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
        let word_freqs: Vec<f64> = m
            .word_log_probs
            .iter()
            .map(|&logp| ((logp + m.total_log) as f64).exp())
            .collect();
        Dict {
            automaton,
            tag_names: m.tag_names,
            word_tags: m.word_tags,
            word_log_probs: m.word_log_probs,
            word_freqs,
            overridden_main_ids: HashSet::new(),
            variant_map: m.variant_map,
            user: None,
        }
    }

    /// 載入自訂詞典（建構期呼叫一次）。
    ///
    /// - 詞先經 `normalize`，重複詞條後者覆蓋前者。
    /// - 僅接受 2 至 255 字元、有限正頻率；主詞典同詞視為覆寫。
    /// - 主詞典與 runtime 詞條合併後重新計算 total 與全部 logp。
    /// - 新詞性字串直接擴充 `tag_names`；超過 u8 id 空間（256）回傳錯誤。
    pub fn install_user_dict(&mut self, entries: &[UserDictEntry]) -> Result<(), String> {
        // 去重（後者覆蓋）並保持穩定順序，供自動機編 id。
        let mut index: HashMap<String, usize> = HashMap::new();
        let mut words: Vec<String> = Vec::new();
        let mut tags: Vec<u8> = Vec::new();
        let mut frequencies: Vec<f64> = Vec::new();
        for e in entries {
            let word = self.normalize(&e.word).into_owned();
            let char_len = word.chars().count();
            if !(2..=u8::MAX as usize).contains(&char_len) {
                return Err(format!("自訂詞 {word:?} 必須包含 2 至 255 個字元"));
            }
            let tag_name = e.tag.as_deref().unwrap_or("Na");
            let tag_id = match self.tag_names.iter().position(|t| t == tag_name) {
                Some(i) => i as u8,
                None => {
                    if self.tag_names.len() > u8::MAX as usize {
                        return Err(format!("詞性表已滿（256），無法新增詞性 {tag_name}"));
                    }
                    self.tag_names.push(tag_name.to_string());
                    (self.tag_names.len() - 1) as u8
                }
            };
            let frequency = e.freq.ok_or_else(|| {
                format!("自訂詞 {word:?} 缺少頻率；0.3.0 起 runtime 詞典必須提供正頻率")
            })?;
            if !frequency.is_finite() || frequency <= 0.0 {
                return Err(format!("自訂詞 {word:?} 的頻率必須是有限正數"));
            }
            match index.get(&word) {
                Some(&i) => {
                    tags[i] = tag_id;
                    frequencies[i] = frequency;
                }
                None => {
                    index.insert(word.clone(), words.len());
                    words.push(word);
                    tags.push(tag_id);
                    frequencies.push(frequency);
                }
            }
        }
        if words.is_empty() {
            return Ok(());
        }

        let mut overridden_main_ids = HashSet::new();
        for word in &words {
            for found in self.automaton.find_overlapping_iter(word) {
                if found.start() == 0 && found.end() == word.len() {
                    overridden_main_ids.insert(found.value());
                }
            }
        }
        let base_total: f64 = self.word_freqs.iter().sum();
        let overridden_total: f64 = overridden_main_ids
            .iter()
            .map(|&id| self.word_freqs[id as usize])
            .sum();
        let total = base_total - overridden_total + frequencies.iter().sum::<f64>();
        if !total.is_finite() || total <= 0.0 {
            return Err("runtime 詞典合併後總頻率不合法".into());
        }
        let total_log = total.ln();
        self.word_log_probs = self
            .word_freqs
            .iter()
            .map(|frequency| (frequency.ln() - total_log) as f32)
            .collect();
        self.overridden_main_ids = overridden_main_ids;
        let log_probs: Vec<f32> = frequencies
            .iter()
            .map(|frequency| (frequency.ln() - total_log) as f32)
            .collect();

        let id_base = self.word_tags.len() as u32;
        let patterns = words
            .iter()
            .enumerate()
            .map(|(i, w)| (w.as_str(), id_base + i as u32));
        let automaton = CharwiseDoubleArrayAhoCorasick::<u32>::with_values(patterns)
            .map_err(|e| format!("自訂詞典自動機建構失敗: {e}"))?;
        self.user = Some(UserDict {
            automaton,
            tags,
            log_probs,
            id_base,
        });
        Ok(())
    }

    /// 一次掃描回傳文字（須已正規化）中的所有詞典命中，byte 區間可重疊。
    /// 主詞典與自訂詞典的命中串接輸出；同一區間兩邊皆命中時由呼叫端
    /// 依 log 機率取捨（DAG DP 與詞性回查皆取機率較高者）。
    #[inline]
    pub fn matches<'s>(&'s self, normalized: &'s str) -> impl Iterator<Item = DictMatch> + 's {
        let main = self
            .automaton
            .find_overlapping_iter(normalized)
            .filter(move |m| !self.overridden_main_ids.contains(&m.value()))
            .map(|m| DictMatch {
                byte_start: m.start(),
                byte_end: m.end(),
                word_id: m.value(),
            });
        let user = self.user.iter().flat_map(move |u| {
            u.automaton
                .find_overlapping_iter(normalized)
                .map(|m| DictMatch {
                    byte_start: m.start(),
                    byte_end: m.end(),
                    word_id: m.value(),
                })
        });
        main.chain(user)
    }

    /// 詞條的 ln(freq/total)。
    #[inline]
    pub fn log_prob(&self, word_id: u32) -> f32 {
        match &self.user {
            Some(u) if word_id >= u.id_base => u.log_probs[(word_id - u.id_base) as usize],
            _ => self.word_log_probs[word_id as usize],
        }
    }

    /// 是否為 runtime／curated 覆寫詞條。
    #[inline]
    pub fn is_user(&self, word_id: u32) -> bool {
        self.user
            .as_ref()
            .is_some_and(|user| word_id >= user.id_base)
    }
    /// 詞條的詞性 id（索引 `tag_names`）。
    #[inline]
    pub fn tag(&self, word_id: u32) -> u8 {
        match &self.user {
            Some(u) if word_id >= u.id_base => u.tags[(word_id - u.id_base) as usize],
            _ => self.word_tags[word_id as usize],
        }
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
#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::DictModel;

    fn tiny_dict() -> Dict {
        let words = ["甲乙", "乙丙"];
        let automaton = CharwiseDoubleArrayAhoCorasick::<u32>::with_values(
            words
                .iter()
                .enumerate()
                .map(|(id, word)| (*word, id as u32)),
        )
        .unwrap();
        Dict::from_model(DictModel {
            automaton_bytes: automaton.serialize(),
            tag_names: vec!["Na".into()],
            word_tags: vec![0, 0],
            word_log_probs: vec![0.5f32.ln(), 0.5f32.ln()],
            word_char_lens: vec![2, 2],
            total_log: 20.0f32.ln(),
            variant_map: vec![],
        })
    }

    #[test]
    fn runtime_dictionary_rejects_invalid_entries() {
        for entry in [
            UserDictEntry {
                word: "甲".into(),
                freq: Some(1.0),
                tag: None,
            },
            UserDictEntry {
                word: "甲乙".into(),
                freq: None,
                tag: None,
            },
            UserDictEntry {
                word: "甲乙".into(),
                freq: Some(0.0),
                tag: None,
            },
            UserDictEntry {
                word: "甲乙".into(),
                freq: Some(f64::NAN),
                tag: None,
            },
        ] {
            assert!(tiny_dict().install_user_dict(&[entry]).is_err());
        }
    }

    #[test]
    fn runtime_override_recomputes_the_combined_total() {
        let mut dict = tiny_dict();
        dict.install_user_dict(&[UserDictEntry {
            word: "甲乙".into(),
            freq: Some(30.0),
            tag: Some("Na".into()),
        }])
        .unwrap();
        let matches: Vec<_> = dict.matches("甲乙乙丙").collect();
        let overridden: Vec<_> = matches
            .iter()
            .filter(|item| item.byte_start == 0 && item.byte_end == 6)
            .collect();
        assert_eq!(overridden.len(), 1, "主詞典同詞必須被 runtime 詞條取代");
        assert!((dict.log_prob(overridden[0].word_id) - (0.75f32).ln()).abs() < 1e-5);
        let base = matches.iter().find(|item| item.byte_start == 6).unwrap();
        assert!((dict.log_prob(base.word_id) - (0.25f32).ln()).abs() < 1e-5);
    }
}
