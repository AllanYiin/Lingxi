//! Python binding：`lingxi._core`。
//!
//! Python 層介面（見 python/lingxi/__init__.py 的薄包裝）：
//!   seg = lingxi.Segmenter()               # 預設載入 wheel 內附模型
//!   seg.cut("文字")                         # -> list[str]
//!   seg.tokenize("文字")                    # -> list[Token]，offset 為字元（code point）座標
//!   seg.cut_batch(texts)                   # rayon 平行，釋放 GIL
//!
//! byte offset → 字元 offset 的轉換在輸出時單趟完成，符合 Python 切片直覺。

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

/// 帶詞性與字元位置的分詞結果。
#[pyclass(frozen, get_all)]
struct Token {
    /// 詞（原文切片的複本；跨 FFI 邊界必須複製）。
    word: String,
    /// 詞性名稱（統一詞性表）。
    tag: String,
    /// 起始字元（code point）位置。
    start: usize,
    /// 結束字元位置（exclusive）。
    end: usize,
}

/// 原文中的一句（Python 字元座標）。
#[pyclass(frozen, get_all)]
struct Sentence {
    text: String,
    start: usize,
    end: usize,
    index: usize,
}

/// 原文中的一個結構感知子句（Python 字元座標）。
#[pyclass(frozen, get_all)]
struct Clause {
    text: String,
    start: usize,
    end: usize,
    sentence_index: usize,
    clause_index: usize,
    list_item: bool,
}

/// 抽取式摘要句（Python 字元座標）。
#[pyclass(frozen, get_all)]
struct SummarySentence {
    text: String,
    start: usize,
    end: usize,
    index: usize,
    clause_index: usize,
    weight: f32,
    explainability: f32,
    novelty: f32,
    coverage_gain: f32,
    proper_noun_count: usize,
    negation_count: usize,
    emphasis_count: usize,
    list_item: bool,
    object_name_count: usize,
    date_count: usize,
    number_count: usize,
    quantity_count: usize,
    acronym_count: usize,
}

/// 關鍵短語及其原文位置（Python 字元座標）。
#[pyclass(frozen, get_all)]
struct Keyphrase {
    phrase: String,
    weight: f32,
    occurrences: usize,
    spans: Vec<(usize, usize)>,
}

#[pymethods]
impl Token {
    fn __repr__(&self) -> String {
        format!(
            "Token({:?}, {:?}, {}, {})",
            self.word, self.tag, self.start, self.end
        )
    }
}

/// 帶詞性與可選情感的字元位置結果。
#[pyclass(frozen, get_all)]
struct AnnotatedToken {
    word: String,
    tag: String,
    start: usize,
    end: usize,
    polarity: Option<String>,
    emotions: Vec<String>,
    context_dependent: bool,
    semantic_flags: Vec<String>,
    appraisals: Vec<String>,
    source: Option<String>,
    domain: Option<String>,
    priority: Option<i8>,
    affect_source: Option<String>,
}

/// 分詞器。執行緒安全，可在多執行緒間共享。
#[pyclass(frozen)]
struct Segmenter {
    inner: lingxi_core::Segmenter,
}

#[pymethods]
impl Segmenter {
    /// 從資產目錄建立（目錄需含 dict.bin / hmm_bmes.bin / hmm_pos.bin）。
    /// `user_dict` 為 jieba 格式詞條行（`詞 [頻率] [詞性]`）；檔案讀取由
    /// Python 層包裝（見 __init__.py 的 load()）。
    #[new]
    #[pyo3(signature = (asset_dir, user_dict=None, lexicons=None))]
    fn new(
        asset_dir: &str,
        user_dict: Option<Vec<String>>,
        lexicons: Option<Vec<String>>,
    ) -> PyResult<Self> {
        let entries = user_dict
            .map(|lines| lingxi_core::parse_user_dict(&lines.join("\n")))
            .unwrap_or_default();
        let custom_lexicons = lexicons
            .unwrap_or_default()
            .iter()
            .map(|text| lingxi_core::parse_custom_lexicon(text))
            .collect::<Result<Vec<_>, _>>()
            .map_err(PyValueError::new_err)?;
        lingxi_core::Segmenter::from_asset_dir_with_user_dict_and_options(
            asset_dir,
            &entries,
            lingxi_core::SegmenterOptions { custom_lexicons },
        )
        .map(|inner| Segmenter { inner })
        .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    /// TextRank 關鍵字抽取 → [(詞, 權重)]，權重降冪。
    /// `allow_tags` 指定候選詞性白名單；預設為名詞類/動詞/英文詞。
    #[pyo3(signature = (
        text,
        top_k=20,
        allow_tags=None,
        proper_noun_enabled=true,
        proper_noun_weight=0.25,
        proper_noun_max_ratio=0.4,
        window_size=5,
        damping=0.85,
        max_iterations=10,
        tolerance=None,
        min_chars=2,
        stopwords=None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn extract_keywords(
        &self,
        text: &str,
        top_k: usize,
        allow_tags: Option<Vec<String>>,
        proper_noun_enabled: bool,
        proper_noun_weight: f32,
        proper_noun_max_ratio: f32,
        window_size: usize,
        damping: f32,
        max_iterations: usize,
        tolerance: Option<f32>,
        min_chars: usize,
        stopwords: Option<Vec<String>>,
    ) -> Vec<(String, f32)> {
        let tag_refs: Option<Vec<&str>> = allow_tags
            .as_ref()
            .map(|v| v.iter().map(String::as_str).collect());
        self.inner
            .extract_keywords_configured(
                text,
                top_k,
                tag_refs.as_deref(),
                &lingxi_core::KeywordExtractionOptions {
                    rank: lingxi_core::TextRankOptions {
                        window_size,
                        damping,
                        max_iterations,
                        tolerance,
                    },
                    min_chars,
                    stopwords: stopwords.unwrap_or_default(),
                    proper_noun: lingxi_core::KeywordOptions {
                        proper_noun_enabled,
                        proper_noun_weight,
                        proper_noun_max_ratio,
                    },
                },
            )
            .into_iter()
            .map(|k| (k.word, k.weight))
            .collect()
    }

    /// 中文斷句，位置為 Python code point offset。
    #[pyo3(signature = (text, semicolon_boundary=false))]
    fn split_sentences(&self, text: &str, semicolon_boundary: bool) -> Vec<Sentence> {
        self.inner
            .split_sentences_with_options(
                text,
                lingxi_core::SentenceSplitOptions { semicolon_boundary },
            )
            .into_iter()
            .map(|sentence| Sentence {
                start: byte_to_char(text, sentence.byte_start),
                end: byte_to_char(text, sentence.byte_end),
                index: sentence.sentence_index,
                text: sentence.text,
            })
            .collect()
    }

    /// 結構感知子句抽取；強調、括號、引號與行內程式碼內容不會被拆開。
    #[pyo3(signature = (text, comma_boundary=true, semicolon_boundary=true, colon_boundary=true))]
    fn split_clauses(
        &self,
        text: &str,
        comma_boundary: bool,
        semicolon_boundary: bool,
        colon_boundary: bool,
    ) -> Vec<Clause> {
        self.inner
            .split_clauses_with_options(
                text,
                lingxi_core::ClauseSplitOptions {
                    comma_boundary,
                    semicolon_boundary,
                    colon_boundary,
                },
            )
            .into_iter()
            .map(|clause| Clause {
                start: byte_to_char(text, clause.byte_start),
                end: byte_to_char(text, clause.byte_end),
                sentence_index: clause.sentence_index,
                clause_index: clause.clause_index,
                list_item: clause.list_item,
                text: clause.text,
            })
            .collect()
    }

    /// TextRank 抽取式摘要；回傳原句、位置、索引與權重。
    #[pyo3(signature = (
        text,
        top_k=3,
        min_sentence_chars=8,
        min_token_chars=1,
        stopwords=None,
        similarity="bm25",
        preserve_order=true,
        redundancy_threshold=Some(0.8),
        min_explainability=Some(0.35),
        comma_boundary=true,
        semicolon_boundary=true,
        colon_boundary=true
    ))]
    #[allow(clippy::too_many_arguments)]
    fn extract_summary(
        &self,
        text: &str,
        top_k: usize,
        min_sentence_chars: usize,
        min_token_chars: usize,
        stopwords: Option<Vec<String>>,
        similarity: &str,
        preserve_order: bool,
        redundancy_threshold: Option<f32>,
        min_explainability: Option<f32>,
        comma_boundary: bool,
        semicolon_boundary: bool,
        colon_boundary: bool,
    ) -> PyResult<Vec<SummarySentence>> {
        let similarity = match similarity {
            "bm25" => lingxi_core::SentenceSimilarity::Bm25,
            "lexical" | "overlap" => lingxi_core::SentenceSimilarity::LexicalOverlap,
            other => {
                return Err(PyValueError::new_err(format!(
                    "未知相似度 {other:?}（可用 bm25|lexical）"
                )))
            }
        };
        Ok(self
            .inner
            .extract_summary_with_options(
                text,
                top_k,
                &lingxi_core::SummaryOptions {
                    min_sentence_chars,
                    min_token_chars,
                    stopwords: stopwords.unwrap_or_default(),
                    similarity,
                    redundancy_threshold,
                    min_explainability,
                    preserve_original_order: preserve_order,
                    comma_boundary,
                    semicolon_boundary,
                    colon_boundary,
                    ..lingxi_core::SummaryOptions::default()
                },
            )
            .into_iter()
            .map(|sentence| SummarySentence {
                start: byte_to_char(text, sentence.byte_start),
                end: byte_to_char(text, sentence.byte_end),
                index: sentence.sentence_index,
                clause_index: sentence.clause_index,
                text: sentence.text,
                weight: sentence.weight,
                explainability: sentence.explainability,
                novelty: sentence.novelty,
                coverage_gain: sentence.coverage_gain,
                proper_noun_count: sentence.signals.proper_noun_count,
                negation_count: sentence.signals.negation_count,
                emphasis_count: sentence.signals.emphasis_count,
                list_item: sentence.signals.list_item,
                object_name_count: sentence.signals.object_name_count,
                date_count: sentence.signals.date_count,
                number_count: sentence.signals.number_count,
                quantity_count: sentence.signals.quantity_count,
                acronym_count: sentence.signals.acronym_count,
            })
            .collect())
    }

    /// 由相鄰高排名關鍵詞組成關鍵短語。
    #[pyo3(signature = (
        text,
        top_k=10,
        keyword_count=None,
        min_occurrences=1,
        max_terms=4,
        stopwords=None
    ))]
    fn extract_keyphrases(
        &self,
        text: &str,
        top_k: usize,
        keyword_count: Option<usize>,
        min_occurrences: usize,
        max_terms: usize,
        stopwords: Option<Vec<String>>,
    ) -> Vec<Keyphrase> {
        self.inner
            .extract_keyphrases_with_options(
                text,
                &lingxi_core::KeyphraseOptions {
                    top_k,
                    keyword_count: keyword_count.unwrap_or_else(|| top_k.saturating_mul(4).max(20)),
                    min_occurrences,
                    max_terms,
                    keywords: lingxi_core::KeywordExtractionOptions {
                        stopwords: stopwords.unwrap_or_default(),
                        ..lingxi_core::KeywordExtractionOptions::default()
                    },
                },
            )
            .into_iter()
            .map(|phrase| Keyphrase {
                phrase: phrase.phrase,
                weight: phrase.weight,
                occurrences: phrase.occurrences,
                spans: phrase
                    .spans
                    .into_iter()
                    .map(|span| {
                        (
                            byte_to_char(text, span.byte_start),
                            byte_to_char(text, span.byte_end),
                        )
                    })
                    .collect(),
            })
            .collect()
    }

    /// 分詞 → 詞列表。
    fn cut(&self, text: &str) -> Vec<String> {
        self.inner
            .cut(text)
            .into_iter()
            .map(str::to_string)
            .collect()
    }

    /// 分詞＋詞性 → Token 列表（start/end 為字元座標）。
    fn tokenize(&self, text: &str) -> Vec<Token> {
        tokens_of(&self.inner, text)
    }

    /// 分詞＋詞性＋詞級情感。
    fn annotate(&self, text: &str) -> Vec<AnnotatedToken> {
        annotated_tokens_of(&self.inner, text)
    }

    /// 批次分詞：釋放 GIL 並以 rayon 平行，輸出順序與輸入一致。
    fn cut_batch(&self, py: Python<'_>, texts: Vec<String>) -> Vec<Vec<String>> {
        py.allow_threads(|| {
            texts
                .par_iter()
                .map(|t| self.inner.cut(t).into_iter().map(str::to_string).collect())
                .collect()
        })
    }

    /// 批次分詞＋詞性：釋放 GIL 並以 rayon 平行。
    fn tokenize_batch(&self, py: Python<'_>, texts: Vec<String>) -> Vec<Vec<Token>> {
        py.allow_threads(|| {
            texts
                .par_iter()
                .map(|t| tokens_of(&self.inner, t))
                .collect()
        })
    }
}

/// 分詞並轉為 Python Token（byte offset → 字元 offset 單趟轉換）。
fn tokens_of(seg: &lingxi_core::Segmenter, text: &str) -> Vec<Token> {
    let raw = seg.tokenize(text);
    let mut out = Vec::with_capacity(raw.len());
    // tokens 依 byte 位置遞增且無縫覆蓋，游標單趟前進即可換算字元位置。
    let mut cursor_byte = 0usize;
    let mut cursor_char = 0usize;
    for t in raw {
        cursor_char += text[cursor_byte..t.byte_start].chars().count();
        let word = &text[t.byte_start..t.byte_end];
        let char_len = word.chars().count();
        out.push(Token {
            word: word.to_string(),
            tag: seg.tag_name(t.tag).to_string(),
            start: cursor_char,
            end: cursor_char + char_len,
        });
        cursor_byte = t.byte_end;
        cursor_char += char_len;
    }
    out
}

fn byte_to_char(text: &str, byte: usize) -> usize {
    text[..byte].chars().count()
}

fn polarity_name(value: lingxi_core::Polarity) -> String {
    match value {
        lingxi_core::Polarity::Positive => "positive",
        lingxi_core::Polarity::Negative => "negative",
        lingxi_core::Polarity::Neutral => "neutral",
        lingxi_core::Polarity::Mixed => "mixed",
        lingxi_core::Polarity::Contextual => "contextual",
    }
    .into()
}

fn annotated_tokens_of(seg: &lingxi_core::Segmenter, text: &str) -> Vec<AnnotatedToken> {
    let raw = seg.annotate(text);
    let mut out = Vec::with_capacity(raw.len());
    let mut cursor_byte = 0usize;
    let mut cursor_char = 0usize;
    for item in raw {
        let token = item.token;
        cursor_char += text[cursor_byte..token.byte_start].chars().count();
        let word = &text[token.byte_start..token.byte_end];
        let char_len = word.chars().count();
        let (polarity, emotions, context_dependent, semantic_flags, appraisals, affect_source) =
            match item.affect {
                Some(affect) => (
                    Some(polarity_name(affect.polarity)),
                    affect.emotions,
                    affect.context_dependent,
                    affect.semantic_flags,
                    affect.appraisals,
                    affect.source,
                ),
                None => (None, Vec::new(), false, Vec::new(), Vec::new(), None),
            };
        let (source, domain, priority) = match item.source {
            Some(source) => (Some(source.id), Some(source.domain), Some(source.priority)),
            None => (None, None, None),
        };
        out.push(AnnotatedToken {
            word: word.to_string(),
            tag: seg.tag_name(token.tag).to_string(),
            start: cursor_char,
            end: cursor_char + char_len,
            polarity,
            emotions,
            context_dependent,
            semantic_flags,
            appraisals,
            source,
            domain,
            priority,
            affect_source,
        });
        cursor_byte = token.byte_end;
        cursor_char += char_len;
    }
    out
}

/// Python 模組進入點。
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Segmenter>()?;
    m.add_class::<Token>()?;
    m.add_class::<AnnotatedToken>()?;
    m.add_class::<Sentence>()?;
    m.add_class::<Clause>()?;
    m.add_class::<SummarySentence>()?;
    m.add_class::<Keyphrase>()?;
    Ok(())
}
