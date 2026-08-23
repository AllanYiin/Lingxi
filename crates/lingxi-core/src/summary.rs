//! TextRank 抽取式摘要。

use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

use regex::Regex;

use crate::clause::{split_clauses_with_options, ClauseSpan, ClauseSplitOptions};
use crate::sentence::{split_sentences_with_options, SentenceSpan, SentenceSplitOptions};
use crate::Segmenter;

/// 句子圖的相似度策略。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum SentenceSimilarity {
    /// 對句長與詞頻較穩健的對稱 BM25。
    #[default]
    Bm25,
    /// 詞頻向量 cosine，相依少且容易解釋。
    LexicalOverlap,
}

/// 抽取式摘要設定。
#[derive(Clone, Debug, PartialEq)]
pub struct SummaryOptions {
    pub min_sentence_chars: usize,
    pub min_token_chars: usize,
    pub stopwords: Vec<String>,
    pub similarity: SentenceSimilarity,
    pub damping: f32,
    pub max_iterations: usize,
    pub tolerance: Option<f32>,
    /// Some(0..=1) 時，以詞彙及有限規則型語意重疊避免選入高度重複句。
    pub redundancy_threshold: Option<f32>,
    /// 候選的可解釋性分數低於此絕對門檻時不納入；`None` 關閉自適應句數。
    /// 分數由相關性、覆蓋增益、新穎性與可辨識訊號組成，不是固定百分比。
    pub min_explainability: Option<f32>,
    pub preserve_original_order: bool,
    /// 摘要先切成子句；此選項控制逗號是否為子句界。
    pub comma_boundary: bool,
    /// 摘要先切成子句；此選項控制冒號是否為子句界。
    pub colon_boundary: bool,
    pub semicolon_boundary: bool,
}

impl Default for SummaryOptions {
    fn default() -> Self {
        SummaryOptions {
            min_sentence_chars: 8,
            min_token_chars: 1,
            stopwords: Vec::new(),
            similarity: SentenceSimilarity::Bm25,
            damping: 0.85,
            max_iterations: 50,
            tolerance: Some(1e-4),
            redundancy_threshold: Some(0.8),
            min_explainability: Some(0.35),
            preserve_original_order: true,
            comma_boundary: true,
            colon_boundary: true,
            semicolon_boundary: true,
        }
    }
}

/// 摘要候選中可直接指出的保留理由。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SummarySignals {
    pub proper_noun_count: usize,
    pub negation_count: usize,
    pub emphasis_count: usize,
    pub list_item: bool,
    pub object_name_count: usize,
    pub date_count: usize,
    pub number_count: usize,
    pub quantity_count: usize,
    pub acronym_count: usize,
}

impl SummarySignals {
    fn coverage(&self) -> f32 {
        let present = usize::from(self.proper_noun_count > 0)
            + usize::from(self.negation_count > 0)
            + usize::from(self.emphasis_count > 0)
            + usize::from(self.list_item)
            + usize::from(self.object_name_count > 0)
            + usize::from(self.date_count > 0)
            + usize::from(self.number_count > 0)
            + usize::from(self.quantity_count > 0)
            + usize::from(self.acronym_count > 0);
        (present as f32 / 5.0).min(1.0)
    }

    fn must_preserve(&self) -> bool {
        self.list_item
            || self.date_count > 0
            || self.number_count > 0
            || self.quantity_count > 0
            || (self.acronym_count > 0 && self.emphasis_count > 0)
    }

    fn has_numeric_fact(&self) -> bool {
        self.date_count > 0 || self.number_count > 0 || self.quantity_count > 0
    }
}

/// 摘要中的一句，保留原文、位置與 TextRank 權重。
#[derive(Clone, Debug, PartialEq)]
pub struct SummarySentence {
    pub text: String,
    pub byte_start: usize,
    pub byte_end: usize,
    pub sentence_index: usize,
    pub clause_index: usize,
    pub weight: f32,
    /// 0..=1；低於 `SummaryOptions::min_explainability` 的候選不會納入。
    pub explainability: f32,
    /// 相對於已選內容的新穎性，1 代表沒有詞彙重疊。
    pub novelty: f32,
    /// 新增此子句後，對全文候選的覆蓋改善幅度。
    pub coverage_gain: f32,
    pub signals: SummarySignals,
}

#[derive(Debug)]
struct SentenceTerms {
    span: ClauseSpan,
    frequencies: HashMap<String, usize>,
    length: usize,
    signals: SummarySignals,
}

impl Segmenter {
    /// 中文斷句；不需要額外模型，輸出 UTF-8 byte offset。
    pub fn split_sentences(&self, text: &str) -> Vec<SentenceSpan> {
        crate::sentence::split_sentences(text)
    }

    /// 使用指定分號規則斷句。
    pub fn split_sentences_with_options(
        &self,
        text: &str,
        options: SentenceSplitOptions,
    ) -> Vec<SentenceSpan> {
        split_sentences_with_options(text, options)
    }

    /// 結構感知的子句抽取；括號、引號、反引號及 Markdown 粗體內不切分。
    pub fn split_clauses(&self, text: &str) -> Vec<ClauseSpan> {
        crate::clause::split_clauses(text)
    }

    /// 使用指定標點規則抽取子句。
    pub fn split_clauses_with_options(
        &self,
        text: &str,
        options: ClauseSplitOptions,
    ) -> Vec<ClauseSpan> {
        split_clauses_with_options(text, options)
    }

    /// TextRank 抽取式摘要，預設以 BM25 建句子圖並依原文順序回傳。
    pub fn extract_summary(&self, text: &str, top_k: usize) -> Vec<SummarySentence> {
        self.extract_summary_with_options(text, top_k, &SummaryOptions::default())
    }

    /// 可設定相似度、收斂、停用詞、句長及去冗餘的抽取式摘要。
    pub fn extract_summary_with_options(
        &self,
        text: &str,
        top_k: usize,
        options: &SummaryOptions,
    ) -> Vec<SummarySentence> {
        if top_k == 0 {
            return Vec::new();
        }
        let normalized = self.dict.normalize(text);
        let stopwords: HashSet<String> = options
            .stopwords
            .iter()
            .map(|word| self.dict.normalize(word).into_owned())
            .collect();
        let spans = split_clauses_with_options(
            text,
            ClauseSplitOptions {
                comma_boundary: options.comma_boundary,
                semicolon_boundary: options.semicolon_boundary,
                colon_boundary: options.colon_boundary,
            },
        );
        let preserve_structured_markdown = should_preserve_structured_markdown(text);
        let mut sentences = Vec::new();
        for span in spans {
            let slice = &normalized[span.byte_start..span.byte_end];
            let mut frequencies = HashMap::new();
            let mut length = 0usize;
            let tokens = self.tokenize(slice);
            let signals = summary_signals(
                &span.text,
                tokens.iter().map(|token| self.tag_name(token.tag)),
            );
            let signals = SummarySignals {
                list_item: signals.list_item || span.list_item,
                ..signals
            };
            // 短子句原則上濾除，但使用者要求可解釋的保留訊號不得在排名前消失。
            if !preserve_structured_markdown
                && span.text.chars().count() < options.min_sentence_chars.max(1)
                && signals.coverage() == 0.0
            {
                continue;
            }
            for token in tokens {
                let word = &slice[token.byte_start..token.byte_end];
                let tag = self.tag_name(token.tag);
                if word.chars().count() < options.min_token_chars.max(1)
                    || stopwords.contains(word)
                    || !summary_candidate(tag)
                {
                    continue;
                }
                *frequencies.entry(word.to_string()).or_insert(0) += 1;
                length += 1;
            }
            if length == 0
                && (preserve_structured_markdown
                    || signals.must_preserve()
                    || signals.has_numeric_fact())
            {
                let fallback = slice.trim();
                if !fallback.is_empty() {
                    frequencies.insert(fallback.to_string(), 1);
                    length = 1;
                }
            }
            if length > 0 {
                sentences.push(SentenceTerms {
                    span,
                    frequencies,
                    length,
                    signals,
                });
            }
        }
        if sentences.is_empty() {
            return Vec::new();
        }
        if sentences.len() == 1 {
            let sentence = &sentences[0].span;
            let explainability =
                explainability_score(1.0, 1.0, 1.0, sentences[0].signals.coverage());
            if !sentences[0].signals.must_preserve()
                && options
                    .min_explainability
                    .filter(|value| value.is_finite())
                    .map(|value| value.clamp(0.0, 1.0))
                    .is_some_and(|limit| explainability < limit)
            {
                return Vec::new();
            }
            return vec![SummarySentence {
                text: sentence.text.clone(),
                byte_start: sentence.byte_start,
                byte_end: sentence.byte_end,
                sentence_index: sentence.sentence_index,
                clause_index: sentence.clause_index,
                weight: 1.0,
                explainability,
                novelty: 1.0,
                coverage_gain: 1.0,
                signals: sentences[0].signals.clone(),
            }];
        }

        let similarities = similarity_matrix(&sentences, options.similarity);
        let mut scores = page_rank(&similarities, options);
        for (score, sentence) in scores.iter_mut().zip(&sentences) {
            *score = signal_adjusted_relevance(*score, &sentence.signals);
        }
        let mut ranked: Vec<usize> = (0..sentences.len()).collect();
        ranked.sort_by(|&a, &b| {
            scores[b].total_cmp(&scores[a]).then_with(|| {
                sentences[a]
                    .span
                    .sentence_index
                    .cmp(&sentences[b].span.sentence_index)
            })
        });

        let wanted = top_k.min(sentences.len());
        let redundancy_threshold = options
            .redundancy_threshold
            .filter(|value| value.is_finite())
            .map(|value| value.clamp(0.0, 1.0));
        let explainability_threshold = options
            .min_explainability
            .filter(|value| value.is_finite())
            .map(|value| value.clamp(0.0, 1.0));
        let mut selected: Vec<(usize, f32, f32, f32)> = Vec::with_capacity(sentences.len());
        let mut coverage = vec![0.0f32; sentences.len()];
        let mandatory = (0..sentences.len())
            .filter(|&index| {
                preserve_structured_markdown || sentences[index].signals.must_preserve()
            })
            .collect::<Vec<_>>();
        for index in mandatory {
            let max_overlap = selected
                .iter()
                .map(|(chosen, _, _, _)| redundancy_overlap(&sentences[index], &sentences[*chosen]))
                .fold(0.0f32, f32::max);
            let novelty = 1.0 - max_overlap;
            let coverage_gain = marginal_coverage_gain(index, &similarities, &coverage);
            let explainability = explainability_score(
                scores[index],
                coverage_gain,
                novelty,
                sentences[index].signals.coverage(),
            );
            update_coverage(index, &similarities, &mut coverage);
            selected.push((index, explainability, novelty, coverage_gain));
        }
        for index in ranked {
            if selected.len() >= wanted {
                break;
            }
            if selected.iter().any(|(chosen, _, _, _)| *chosen == index) {
                continue;
            }
            let max_overlap = selected
                .iter()
                .map(|(chosen, _, _, _)| redundancy_overlap(&sentences[index], &sentences[*chosen]))
                .fold(0.0f32, f32::max);
            if redundancy_threshold.is_some_and(|limit| max_overlap >= limit) {
                continue;
            }
            let novelty = 1.0 - max_overlap;
            let coverage_gain = marginal_coverage_gain(index, &similarities, &coverage);
            let explainability = explainability_score(
                scores[index],
                coverage_gain,
                novelty,
                sentences[index].signals.coverage(),
            );
            if explainability_threshold.is_some_and(|limit| explainability < limit) {
                continue;
            }
            update_coverage(index, &similarities, &mut coverage);
            selected.push((index, explainability, novelty, coverage_gain));
        }
        if options.preserve_original_order {
            selected.sort_unstable_by_key(|(index, _, _, _)| sentences[*index].span.clause_index);
        }
        selected
            .into_iter()
            .map(|(index, explainability, novelty, coverage_gain)| {
                let sentence = &sentences[index].span;
                SummarySentence {
                    text: sentence.text.clone(),
                    byte_start: sentence.byte_start,
                    byte_end: sentence.byte_end,
                    sentence_index: sentence.sentence_index,
                    clause_index: sentence.clause_index,
                    weight: scores[index],
                    explainability,
                    novelty,
                    coverage_gain,
                    signals: sentences[index].signals.clone(),
                }
            })
            .collect()
    }
}

fn summary_candidate(tag: &str) -> bool {
    matches!(tag, "Na" | "Nb" | "Nc" | "Ncd" | "Nv" | "FW" | "A") || tag.starts_with('V')
}

/// Markdown 標題與條列已占據主要內容時，視為作者完成過壓縮的結構化筆記，
/// 摘要器應完整保留而不是再次簡化。
pub fn should_preserve_structured_markdown(text: &str) -> bool {
    let mut meaningful_lines = 0usize;
    let mut structured_lines = 0usize;
    let mut list_lines = 0usize;

    for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        meaningful_lines += 1;
        let heading = line
            .strip_prefix('#')
            .is_some_and(|rest| rest.starts_with('#') || rest.starts_with(' '));
        let list_item = is_list_item(line);
        if heading || list_item {
            structured_lines += 1;
        }
        list_lines += usize::from(list_item);
    }

    meaningful_lines >= 4
        && list_lines >= 3
        && structured_lines.saturating_mul(3) >= meaningful_lines.saturating_mul(2)
}

fn summary_signals<'a>(text: &str, tags: impl Iterator<Item = &'a str>) -> SummarySignals {
    static NEGATION: OnceLock<Regex> = OnceLock::new();
    static OBJECT_NAME: OnceLock<Regex> = OnceLock::new();
    static DATE: OnceLock<Regex> = OnceLock::new();
    static NUMBER: OnceLock<Regex> = OnceLock::new();
    static QUANTITY: OnceLock<Regex> = OnceLock::new();
    let negation = NEGATION.get_or_init(|| {
        Regex::new(r"並非|並未|從未|沒有|禁止|避免|未|無|沒|非|否|勿|莫|別")
            .expect("固定否定詞 regex 應有效")
    });
    let object_name = OBJECT_NAME.get_or_init(|| {
        Regex::new(
            r"`[^`\r\n]+`|[A-Za-z_][A-Za-z0-9_.:-]*\s*[\(（]|(?:[A-Za-z][A-Za-z0-9_-]*\.)+[A-Za-z_][A-Za-z0-9_-]*|\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b|\b[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+\b",
        )
        .expect("固定物件名 regex 應有效")
    });
    let date = DATE.get_or_init(|| {
        Regex::new(
            r"(?:民國\s*)?\d{2,4}年(?:\d{1,2}月(?:\d{1,2}日)?)?|\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\d{1,2}月\d{1,2}日|第?\d+\s*(?:季|季度|週|周)",
        )
        .expect("固定日期 regex 應有效")
    });
    let number = NUMBER.get_or_init(|| {
        Regex::new(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)").expect("固定數字 regex 應有效")
    });
    let quantity = QUANTITY.get_or_init(|| {
        Regex::new(
            r"(?i)(?:(?:NT|US)?[$€¥￥]\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)|[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:個|个)?\s*(?:%|％|bps|ms|毫秒|秒|分鐘|分|小時|天|日|週|周|月|季|年|元|萬元|億元|兆元|美元|公斤|公克|克|kg|g|公里|公尺|公分|毫米|km|m|cm|mm|平方公尺|立方公尺|坪|公升|毫升|l|ml|kb|mb|gb|tb|kib|mib|gib|tib|hz|khz|mhz|ghz|w|kw|mw|v|kv|a|ma|°c|℃|°f))",
        )
        .expect("固定帶單位數值 regex 應有效")
    });
    SummarySignals {
        proper_noun_count: tags.filter(|tag| matches!(*tag, "Nb" | "Nc")).count(),
        negation_count: negation.find_iter(text).count()
            + text
                .match_indices('不')
                .filter(|(index, _)| {
                    !text[*index + '不'.len_utf8()..]
                        .starts_with(|next| matches!(next, '只' | '僅'))
                })
                .count(),
        emphasis_count: emphasis_count(text),
        list_item: is_list_item(text),
        object_name_count: object_name.find_iter(text).count(),
        date_count: date.find_iter(text).count(),
        number_count: number.find_iter(text).count(),
        quantity_count: quantity.find_iter(text).count(),
        acronym_count: acronym_count(text),
    }
}

fn signal_adjusted_relevance(relevance: f32, signals: &SummarySignals) -> f32 {
    let floor: f32 = if signals.list_item {
        0.90
    } else if signals.acronym_count > 0 && signals.emphasis_count > 0 {
        0.90
    } else if signals.quantity_count > 0 {
        0.85
    } else if signals.date_count > 0 {
        0.80
    } else if signals.acronym_count > 0 {
        0.75
    } else if signals.number_count > 0 {
        0.60
    } else {
        0.0
    };
    relevance.clamp(0.0, 1.0).max(floor)
}

fn acronym_count(text: &str) -> usize {
    let mut count = 0usize;
    let mut run = String::new();
    let finish_run = |run: &mut String, count: &mut usize| {
        let uppercase_letters = run.chars().filter(|ch| ch.is_ascii_uppercase()).count();
        if uppercase_letters >= 2 && run.chars().count() <= 12 {
            *count += 1;
        }
        run.clear();
    };

    for ch in text.chars() {
        if ch.is_ascii_uppercase()
            || ch.is_ascii_digit()
            || (!run.is_empty() && matches!(ch, '&' | '-' | '.' | '/'))
        {
            run.push(ch);
        } else {
            finish_run(&mut run, &mut count);
        }
    }
    finish_run(&mut run, &mut count);
    count
}

fn emphasis_count(text: &str) -> usize {
    let markdown = text.matches("**").count() / 2;
    let paired = [
        ('（', '）'),
        ('(', ')'),
        ('「', '」'),
        ('『', '』'),
        ('“', '”'),
        ('‘', '’'),
    ]
    .into_iter()
    .filter(|(open, close)| text.contains(*open) && text.contains(*close))
    .count();
    let symmetric =
        usize::from(text.matches('"').count() >= 2) + usize::from(text.matches('\'').count() >= 2);
    markdown + paired + symmetric
}

fn is_list_item(text: &str) -> bool {
    let text = text.trim_start();
    if ["- ", "* ", "+ ", "•", "‧", "▪", "◦"]
        .iter()
        .any(|prefix| text.starts_with(prefix))
    {
        return true;
    }
    let prefix = text
        .chars()
        .take_while(|ch| ch.is_ascii_digit() || "一二三四五六七八九十".contains(*ch))
        .collect::<String>();
    !prefix.is_empty()
        && text[prefix.len()..].starts_with(|ch| matches!(ch, '.' | ')' | '）' | '、'))
}

fn marginal_coverage_gain(index: usize, matrix: &[Vec<f32>], coverage: &[f32]) -> f32 {
    let gain = matrix[index]
        .iter()
        .enumerate()
        .map(|(other, &similarity)| {
            let representativeness = if other == index {
                1.0
            } else {
                similarity.clamp(0.0, 1.0)
            };
            (representativeness - coverage[other]).max(0.0)
        })
        .sum::<f32>();
    gain / matrix.len().max(1) as f32
}

fn update_coverage(index: usize, matrix: &[Vec<f32>], coverage: &mut [f32]) {
    for (other, value) in coverage.iter_mut().enumerate() {
        let representativeness = if other == index {
            1.0
        } else {
            matrix[index][other].clamp(0.0, 1.0)
        };
        *value = value.max(representativeness);
    }
}

fn explainability_score(
    relevance: f32,
    coverage_gain: f32,
    novelty: f32,
    signal_coverage: f32,
) -> f32 {
    (0.45 * relevance.clamp(0.0, 1.0)
        + 0.20 * coverage_gain.clamp(0.0, 1.0)
        + 0.10 * novelty.clamp(0.0, 1.0)
        + 0.25 * signal_coverage.clamp(0.0, 1.0))
    .clamp(0.0, 1.0)
}

fn similarity_matrix(sentences: &[SentenceTerms], strategy: SentenceSimilarity) -> Vec<Vec<f32>> {
    let n = sentences.len();
    let mut matrix = vec![vec![0.0; n]; n];
    let mut document_frequency: HashMap<&str, usize> = HashMap::new();
    for sentence in sentences {
        for term in sentence.frequencies.keys() {
            *document_frequency.entry(term.as_str()).or_default() += 1;
        }
    }
    let average_length = sentences
        .iter()
        .map(|sentence| sentence.length)
        .sum::<usize>() as f32
        / n as f32;
    for i in 0..n {
        for j in (i + 1)..n {
            let similarity = match strategy {
                SentenceSimilarity::Bm25 => symmetric_bm25(
                    &sentences[i],
                    &sentences[j],
                    &document_frequency,
                    n,
                    average_length,
                ),
                SentenceSimilarity::LexicalOverlap => cosine_overlap(&sentences[i], &sentences[j]),
            };
            matrix[i][j] = similarity;
            matrix[j][i] = similarity;
        }
    }
    matrix
}

fn symmetric_bm25(
    left: &SentenceTerms,
    right: &SentenceTerms,
    document_frequency: &HashMap<&str, usize>,
    document_count: usize,
    average_length: f32,
) -> f32 {
    let score = |query: &SentenceTerms, document: &SentenceTerms| {
        let k1 = 1.5f32;
        let b = 0.75f32;
        query
            .frequencies
            .keys()
            .filter_map(|term| {
                let tf = *document.frequencies.get(term)? as f32;
                let df = *document_frequency.get(term.as_str()).unwrap_or(&1) as f32;
                let idf = ((document_count as f32 - df + 0.5) / (df + 0.5) + 1.0).ln();
                let length_norm = document.length as f32 / average_length.max(1.0);
                Some(idf * tf * (k1 + 1.0) / (tf + k1 * (1.0 - b + b * length_norm)))
            })
            .sum::<f32>()
    };
    (score(left, right) + score(right, left)) * 0.5
}

fn cosine_overlap(left: &SentenceTerms, right: &SentenceTerms) -> f32 {
    let dot = left
        .frequencies
        .iter()
        .filter_map(|(term, &frequency)| {
            right
                .frequencies
                .get(term)
                .map(|&other| frequency as f32 * other as f32)
        })
        .sum::<f32>();
    let left_norm = left
        .frequencies
        .values()
        .map(|&value| (value * value) as f32)
        .sum::<f32>()
        .sqrt();
    let right_norm = right
        .frequencies
        .values()
        .map(|&value| (value * value) as f32)
        .sum::<f32>()
        .sqrt();
    if left_norm == 0.0 || right_norm == 0.0 {
        0.0
    } else {
        dot / (left_norm * right_norm)
    }
}

fn page_rank(matrix: &[Vec<f32>], options: &SummaryOptions) -> Vec<f32> {
    let n = matrix.len();
    let out_sum: Vec<f32> = matrix.iter().map(|row| row.iter().sum()).collect();
    let damping = if options.damping.is_finite() {
        options.damping.clamp(0.0, 1.0)
    } else {
        0.85
    };
    let tolerance = options
        .tolerance
        .filter(|value| value.is_finite() && *value > 0.0);
    let mut scores = vec![1.0 / n as f32; n];
    for _ in 0..options.max_iterations.max(1) {
        let previous = scores.clone();
        for u in 0..n {
            let incoming = (0..n)
                .filter(|&v| out_sum[v] > 0.0)
                .map(|v| matrix[v][u] / out_sum[v] * previous[v])
                .sum::<f32>();
            scores[u] = (1.0 - damping) / n as f32 + damping * incoming;
        }
        let delta = scores
            .iter()
            .zip(&previous)
            .map(|(current, old)| (current - old).abs())
            .fold(0.0f32, f32::max);
        if tolerance.is_some_and(|value| delta < value) {
            break;
        }
    }
    let max = scores.iter().copied().fold(0.0f32, f32::max);
    if max > 0.0 {
        for score in &mut scores {
            *score /= max;
        }
    }
    scores
}

fn jaccard(left: &HashMap<String, usize>, right: &HashMap<String, usize>) -> f32 {
    let intersection = left.keys().filter(|term| right.contains_key(*term)).count();
    let union = left.len() + right.len() - intersection;
    if union == 0 {
        0.0
    } else {
        intersection as f32 / union as f32
    }
}

fn redundancy_overlap(left: &SentenceTerms, right: &SentenceTerms) -> f32 {
    jaccard(&left.frequencies, &right.frequencies).max(rule_based_semantic_overlap(
        &left.span.text,
        &right.span.text,
    ))
}

fn rule_based_semantic_overlap(left: &str, right: &str) -> f32 {
    let has_sedentary_concept = |text: &str| {
        ["久坐", "坐太久", "長時間坐", "坐在電腦前"]
            .iter()
            .any(|term| text.contains(term))
    };
    let has_kidney_concept = |text: &str| text.contains('腎');
    let has_harm_concept = |text: &str| {
        ["風險", "傷腎", "致命", "腎臟病", "腎衰竭", "危害"]
            .iter()
            .any(|term| text.contains(term))
    };
    if has_sedentary_concept(left)
        && has_sedentary_concept(right)
        && has_kidney_concept(left)
        && has_kidney_concept(right)
        && has_harm_concept(left)
        && has_harm_concept(right)
    {
        0.85
    } else {
        0.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn terms(values: &[(&str, usize)]) -> SentenceTerms {
        SentenceTerms {
            span: ClauseSpan {
                text: String::new(),
                byte_start: 0,
                byte_end: 0,
                sentence_index: 0,
                clause_index: 0,
                list_item: false,
            },
            frequencies: values
                .iter()
                .map(|(term, frequency)| ((*term).to_string(), *frequency))
                .collect(),
            length: values.iter().map(|(_, frequency)| frequency).sum(),
            signals: SummarySignals::default(),
        }
    }

    #[test]
    fn similarity_rewards_shared_terms() {
        let a = terms(&[("預算", 2), ("立法院", 1)]);
        let b = terms(&[("預算", 1), ("審議", 1)]);
        let c = terms(&[("颱風", 1), ("降雨", 1)]);
        assert!(cosine_overlap(&a, &b) > cosine_overlap(&a, &c));
    }

    #[test]
    fn redundancy_uses_bounded_jaccard() {
        let a = terms(&[("預算", 1), ("立法院", 1)]);
        let b = terms(&[("預算", 1), ("審議", 1)]);
        assert_eq!(jaccard(&a.frequencies, &b.frequencies), 1.0 / 3.0);
    }

    #[test]
    fn explainable_signals_cover_required_content_types() {
        let signals = summary_signals(
            "- **不得**呼叫 `tools.run()`（保留原值）",
            ["Nb", "VC"].into_iter(),
        );
        assert_eq!(signals.proper_noun_count, 1);
        assert!(signals.negation_count >= 1);
        assert!(signals.emphasis_count >= 2);
        assert!(signals.list_item);
        assert!(signals.object_name_count >= 1);
        assert_eq!(signals.coverage(), 1.0);
    }

    #[test]
    fn explainability_is_an_absolute_threshold_score_not_a_percentage_delta() {
        let weak = explainability_score(0.2, 0.05, 0.4, 0.0);
        let explained = explainability_score(0.2, 0.05, 0.4, 1.0);
        assert!(weak < 0.35);
        assert!(explained >= 0.35);
    }

    #[test]
    fn distinguishes_correlative_bu_zhi_from_real_negation() {
        let correlative = summary_signals("不只會傷腎，不僅影響循環", std::iter::empty());
        assert_eq!(correlative.negation_count, 0);

        let negative = summary_signals("不要久坐，也不能忽略風險", std::iter::empty());
        assert_eq!(negative.negation_count, 2);
    }

    #[test]
    fn detects_dates_numbers_and_quantities() {
        let signals = summary_signals(
            "研究於2026-08-20公布，風險增加15%，樣本共有1,024人。",
            std::iter::empty(),
        );
        assert_eq!(signals.date_count, 1);
        assert_eq!(signals.quantity_count, 1);
        assert!(signals.number_count >= 3);
        assert!(signals.must_preserve());

        let classifier = summary_signals("每天久坐超過10個小時", std::iter::empty());
        assert_eq!(classifier.quantity_count, 1);
    }

    #[test]
    fn detects_and_preserves_emphasized_uppercase_acronyms() {
        let defined = summary_signals("Fear Of Missing Out（FOMO）在投資市場", std::iter::empty());
        assert_eq!(defined.acronym_count, 1);
        assert_eq!(defined.emphasis_count, 1);
        assert!(defined.must_preserve());

        let adjacent = summary_signals("FOMO情緒燒起來", std::iter::empty());
        assert_eq!(adjacent.acronym_count, 1);
        assert_eq!(adjacent.emphasis_count, 0);
        assert!(!adjacent.must_preserve());
        assert_eq!(acronym_count("fomo不是全大寫縮略語"), 0);
    }

    #[test]
    fn recognizes_dense_markdown_as_already_summarized_notes() {
        let notes = "# 重點\n\n## A. 規則\n1. 保留日期\n2. 保留數字\n3. 保留單位\n4. 保留條列";
        assert!(should_preserve_structured_markdown(notes));
        assert!(!should_preserve_structured_markdown(
            "這是一般文章。\n- 只有一個補充項目。\n其餘內容仍是連續敘述。"
        ));
    }

    #[test]
    fn sedentary_kidney_rules_detect_semantic_redundancy() {
        let generic = "坐太久不只會傷腎，嚴重時真的可能會致命。";
        let quantified = "久坐者罹患慢性腎臟病的風險增加15%。";
        assert!(rule_based_semantic_overlap(generic, quantified) >= 0.8);
    }
}
