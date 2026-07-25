//! 詞典：AC 自動機查詢 + 文字正規化。
//!
//! `Dict` 由 `DictModel` 資產建構，提供一次掃描取得句中所有詞典命中
//! （即 DAG 的全部邊），以及與轉換階段完全一致的文字正規化。

use std::borrow::Cow;
use std::collections::HashMap;

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
    /// 未登入單字的平滑 log 機率 = ln(0.5) - ln(total_freq)。
    pub oov_char_log_prob: f32,
    /// ln(total_freq)：自訂詞條顯式頻率換算 log 機率的基準。
    total_log: f32,
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
        Dict {
            automaton,
            tag_names: m.tag_names,
            word_tags: m.word_tags,
            word_log_probs: m.word_log_probs,
            oov_char_log_prob: 0.5f32.ln() - m.total_log,
            total_log: m.total_log,
            variant_map: m.variant_map,
            user: None,
        }
    }

    /// 載入自訂詞典（建構期呼叫一次）。
    ///
    /// - 詞先經 `normalize`（與查詢端一致），重複詞條後者覆蓋前者。
    /// - 顯式頻率 → ln(freq/total)（0.5 平滑下限）；省略頻率 → 對該詞跑
    ///   主詞典 DAG DP 取最佳切分的 log 機率和，加上小幅餘裕，保證該詞
    ///   恰好贏過現行切分（jieba `suggest_freq` 語意），又不過度擠壓
    ///   與其他詞的跨界競爭。
    /// - 新詞性字串直接擴充 `tag_names`；超過 u8 id 空間（256）回傳錯誤。
    pub fn install_user_dict(&mut self, entries: &[UserDictEntry]) -> Result<(), String> {
        // 贏過現行切分所需的 log 機率餘裕：遠大於 f32 累加誤差、遠小於詞頻級距。
        const WIN_MARGIN: f32 = 1e-3;

        // 去重（後者覆蓋）並保持穩定順序，供自動機編 id。
        let mut index: HashMap<String, usize> = HashMap::new();
        let mut words: Vec<String> = Vec::new();
        let mut tags: Vec<u8> = Vec::new();
        let mut log_probs: Vec<f32> = Vec::new();
        for e in entries {
            let word = self.normalize(&e.word).into_owned();
            if word.is_empty() {
                continue;
            }
            let tag_name = e.tag.as_deref().unwrap_or("n");
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
            let logp = match e.freq {
                Some(f) => (f.max(0.5) as f32).ln() - self.total_log,
                None => self.best_split_log_prob(&word) + WIN_MARGIN,
            };
            match index.get(&word) {
                Some(&i) => {
                    tags[i] = tag_id;
                    log_probs[i] = logp;
                }
                None => {
                    index.insert(word.clone(), words.len());
                    words.push(word);
                    tags.push(tag_id);
                    log_probs.push(logp);
                }
            }
        }
        if words.is_empty() {
            return Ok(());
        }

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

    /// 對單一詞跑主詞典 DAG DP，回傳最佳切分路徑的 log 機率和。
    /// 只在載入自訂詞典時使用（此時 `self.user` 尚未含該詞）。
    fn best_split_log_prob(&self, normalized_word: &str) -> f32 {
        let mut segs = Vec::new();
        crate::dag::cut_dag(self, normalized_word, 0, &mut segs);
        segs.iter()
            .map(|s| match s.kind {
                crate::segment::SegKind::Dict(id) => self.log_prob(id),
                _ => self.oov_char_log_prob,
            })
            .sum()
    }

    /// 一次掃描回傳文字（須已正規化）中的所有詞典命中，byte 區間可重疊。
    /// 主詞典與自訂詞典的命中串接輸出；同一區間兩邊皆命中時由呼叫端
    /// 依 log 機率取捨（DAG DP 與詞性回查皆取機率較高者）。
    #[inline]
    pub fn matches<'s>(&'s self, normalized: &'s str) -> impl Iterator<Item = DictMatch> + 's {
        let main = self
            .automaton
            .find_overlapping_iter(normalized)
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
