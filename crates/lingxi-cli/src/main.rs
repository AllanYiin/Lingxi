//! lingxi CLI：逐行讀 stdin 或檔案，輸出分詞結果。
//!
//! 用法：
//!   lingxi [--assets DIR] [--user-dict FILE] [--lexicon FILE]... [--format words|tsv|jsonl|annotated-json]
//!          [--sep 分隔符] [--keywords N] [FILE...]
//!
//! - words（預設）：一行輸入一行輸出，詞以 --sep（預設 "/"）連接，空白詞段略過
//! - tsv：每詞一行「詞\t詞性」，輸入行之間以空行分隔
//! - jsonl：每行輸入輸出一行 JSON：{"tokens":[{"w":..,"t":..,"s":..,"e":..}]}
//! - --keywords N：改為對全部輸入做 TextRank，輸出 N 行「詞\t權重」後結束
//! - --user-dict：jieba 格式自訂詞典（每行 `詞 [頻率] [詞性]`）
//!
//! 資產目錄搜尋順序：--assets → 環境變數 LINGXI_ASSETS → ./assets。

use std::collections::{BTreeSet, HashMap};
use std::io::{BufRead, BufWriter, Read, Write};
use std::time::Instant;

use anyhow::{bail, Context, Result};
use lingxi_core::Segmenter;
use serde::Serialize;

enum Format {
    Words,
    Tsv,
    Jsonl,
    AnnotatedJson,
}

struct Args {
    assets: String,
    user_dict: Option<String>,
    lexicons: Vec<String>,
    format: Format,
    sep: String,
    files: Vec<String>,
    stats: bool,
    /// Some(n) = 關鍵字模式：全部輸入做 TextRank，取前 n 個。
    keywords: Option<usize>,
    /// Some(n) = 抽取式摘要模式。
    summary: Option<usize>,
    /// Some(n) = 摘要測試報告模式：單一 JSON，含全部子句診斷與 token 比較。
    summary_report: Option<usize>,
    /// Some(n) = 關鍵短語模式。
    keyphrases: Option<usize>,
    /// 中文斷句 JSONL 模式。
    sentences: bool,
    /// 結構感知子句 JSONL 模式。
    clauses: bool,
    /// 一般摘要候選的最低可解釋性；高風險訊號可略過門檻但仍遵守 `top_k`。
    min_explainability: Option<f32>,
    /// 可選停用詞檔，每行一詞。
    stopwords: Option<String>,
}

fn parse_args() -> Result<Args> {
    let mut args = Args {
        assets: std::env::var("LINGXI_ASSETS").unwrap_or_else(|_| "assets".into()),
        user_dict: None,
        lexicons: Vec::new(),
        format: Format::Words,
        sep: "/".into(),
        files: Vec::new(),
        stats: false,
        keywords: None,
        summary: None,
        summary_report: None,
        keyphrases: None,
        sentences: false,
        clauses: false,
        min_explainability: None,
        stopwords: None,
    };
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--assets" => args.assets = it.next().context("--assets 需要參數")?,
            "--user-dict" => args.user_dict = Some(it.next().context("--user-dict 需要參數")?),
            "--lexicon" => args.lexicons.push(it.next().context("--lexicon 需要參數")?),
            "--sep" => args.sep = it.next().context("--sep 需要參數")?,
            "--format" => {
                args.format = match it.next().context("--format 需要參數")?.as_str() {
                    "words" => Format::Words,
                    "tsv" => Format::Tsv,
                    "jsonl" => Format::Jsonl,
                    "annotated-json" => Format::AnnotatedJson,
                    other => bail!("未知格式 {other}（可用 words|tsv|jsonl|annotated-json）"),
                }
            }
            "--keywords" => {
                args.keywords = Some(
                    it.next()
                        .context("--keywords 需要參數")?
                        .parse()
                        .context("--keywords 需為整數")?,
                )
            }
            "--summary" => {
                args.summary = Some(
                    it.next()
                        .context("--summary 需要參數")?
                        .parse()
                        .context("--summary 需為整數")?,
                )
            }
            "--summary-report" => {
                args.summary_report = Some(
                    it.next()
                        .context("--summary-report 需要參數")?
                        .parse()
                        .context("--summary-report 需為整數")?,
                )
            }
            "--keyphrases" => {
                args.keyphrases = Some(
                    it.next()
                        .context("--keyphrases 需要參數")?
                        .parse()
                        .context("--keyphrases 需為整數")?,
                )
            }
            "--sentences" => args.sentences = true,
            "--clauses" => args.clauses = true,
            "--min-explainability" => {
                args.min_explainability = Some(
                    it.next()
                        .context("--min-explainability 需要參數")?
                        .parse()
                        .context("--min-explainability 需為 0 到 1 的數值")?,
                )
            }
            "--stopwords" => args.stopwords = Some(it.next().context("--stopwords 需要參數")?),
            "--stats" => args.stats = true,
            "--help" | "-h" => {
                eprintln!("用法: lingxi [--assets DIR] [--user-dict FILE] [--lexicon FILE]... [--format words|tsv|jsonl|annotated-json] [--sep S] [--keywords N | --summary MAX_N | --summary-report MAX_N | --keyphrases N | --sentences | --clauses] [--min-explainability 0..1] [--stopwords FILE] [--stats] [FILE...]");
                std::process::exit(0);
            }
            _ => args.files.push(a),
        }
    }
    let analysis_modes = usize::from(args.keywords.is_some())
        + usize::from(args.summary.is_some())
        + usize::from(args.summary_report.is_some())
        + usize::from(args.keyphrases.is_some())
        + usize::from(args.sentences)
        + usize::from(args.clauses);
    if analysis_modes > 1 {
        bail!("--keywords、--summary、--summary-report、--keyphrases、--sentences、--clauses 只能擇一");
    }
    if args
        .min_explainability
        .is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value))
    {
        bail!("--min-explainability 必須介於 0 到 1");
    }
    Ok(args)
}

fn main() -> Result<()> {
    let args = parse_args()?;

    let t0 = Instant::now();
    let user_entries = match &args.user_dict {
        Some(path) => {
            let text =
                std::fs::read_to_string(path).with_context(|| format!("讀取自訂詞典 {path}"))?;
            lingxi_core::parse_user_dict(&text)
        }
        None => Vec::new(),
    };
    let lexicons = args
        .lexicons
        .iter()
        .map(|path| {
            let text = std::fs::read_to_string(path)
                .with_context(|| format!("讀取多領域自訂辭典 {path}"))?;
            lingxi_core::parse_custom_lexicon(&text).map_err(anyhow::Error::msg)
        })
        .collect::<Result<Vec<_>>>()?;
    let lexicon_report = summarize_lexicons(&lexicons);
    let seg = Segmenter::from_asset_dir_with_user_dict_and_options(
        &args.assets,
        &user_entries,
        lingxi_core::SegmenterOptions {
            custom_lexicons: lexicons,
        },
    )
    .with_context(|| {
        format!(
            "載入資產目錄 {} 失敗（可用 --assets 或 LINGXI_ASSETS 指定）",
            args.assets
        )
    })?;
    let load_ms = t0.elapsed().as_millis();

    let stdout = std::io::stdout();
    let mut out = BufWriter::new(stdout.lock());

    // 文件分析模式：整份輸入一次處理，與逐行分詞管線互斥。
    if args.keywords.is_some()
        || args.summary.is_some()
        || args.summary_report.is_some()
        || args.keyphrases.is_some()
        || args.sentences
        || args.clauses
    {
        let text = read_all_text(&args.files)?;
        let stopwords = read_stopwords(args.stopwords.as_deref())?;
        if let Some(top_k) = args.keywords {
            for keyword in seg.extract_keywords_configured(
                &text,
                top_k,
                None,
                &lingxi_core::KeywordExtractionOptions {
                    stopwords,
                    ..lingxi_core::KeywordExtractionOptions::default()
                },
            ) {
                writeln!(out, "{}\t{:.4}", keyword.word, keyword.weight)?;
            }
        } else if let Some(top_k) = args.summary_report {
            write_summary_report(
                &mut out,
                &seg,
                &text,
                top_k,
                stopwords,
                args.min_explainability,
            )?;
        } else if let Some(top_k) = args.summary {
            for sentence in seg.extract_summary_with_options(
                &text,
                top_k,
                &lingxi_core::SummaryOptions {
                    stopwords,
                    min_explainability: args
                        .min_explainability
                        .or(lingxi_core::SummaryOptions::default().min_explainability),
                    ..lingxi_core::SummaryOptions::default()
                },
            ) {
                serde_json::to_writer(
                    &mut out,
                    &serde_json::json!({
                        "text": sentence.text,
                        "byteStart": sentence.byte_start,
                        "byteEnd": sentence.byte_end,
                        "index": sentence.sentence_index,
                        "clauseIndex": sentence.clause_index,
                        "weight": sentence.weight,
                        "explainability": sentence.explainability,
                        "novelty": sentence.novelty,
                        "coverageGain": sentence.coverage_gain,
                        "signals": {
                            "properNounCount": sentence.signals.proper_noun_count,
                            "negationCount": sentence.signals.negation_count,
                            "emphasisCount": sentence.signals.emphasis_count,
                            "listItem": sentence.signals.list_item,
                            "objectNameCount": sentence.signals.object_name_count,
                            "dateCount": sentence.signals.date_count,
                            "numberCount": sentence.signals.number_count,
                            "quantityCount": sentence.signals.quantity_count,
                            "acronymCount": sentence.signals.acronym_count,
                        },
                    }),
                )?;
                out.write_all(b"\n")?;
            }
        } else if let Some(top_k) = args.keyphrases {
            for phrase in seg.extract_keyphrases_with_options(
                &text,
                &lingxi_core::KeyphraseOptions {
                    top_k,
                    keyword_count: top_k.saturating_mul(4).max(20),
                    keywords: lingxi_core::KeywordExtractionOptions {
                        stopwords,
                        ..lingxi_core::KeywordExtractionOptions::default()
                    },
                    ..lingxi_core::KeyphraseOptions::default()
                },
            ) {
                serde_json::to_writer(
                    &mut out,
                    &serde_json::json!({
                        "phrase": phrase.phrase,
                        "weight": phrase.weight,
                        "occurrences": phrase.occurrences,
                        "spans": phrase.spans.iter().map(|span| serde_json::json!({
                            "byteStart": span.byte_start,
                            "byteEnd": span.byte_end,
                        })).collect::<Vec<_>>(),
                    }),
                )?;
                out.write_all(b"\n")?;
            }
        } else if args.sentences {
            for sentence in seg.split_sentences(&text) {
                serde_json::to_writer(
                    &mut out,
                    &serde_json::json!({
                        "text": sentence.text,
                        "byteStart": sentence.byte_start,
                        "byteEnd": sentence.byte_end,
                        "index": sentence.sentence_index,
                    }),
                )?;
                out.write_all(b"\n")?;
            }
        } else {
            for clause in seg.split_clauses(&text) {
                serde_json::to_writer(
                    &mut out,
                    &serde_json::json!({
                        "text": clause.text,
                        "byteStart": clause.byte_start,
                        "byteEnd": clause.byte_end,
                        "sentenceIndex": clause.sentence_index,
                        "clauseIndex": clause.clause_index,
                        "listItem": clause.list_item,
                    }),
                )?;
                out.write_all(b"\n")?;
            }
        }
        out.flush()?;
        if args.stats {
            print_resource_stats(&seg, &lexicon_report);
            eprintln!("載入 {load_ms} ms; 分析 {:.2} MB", text.len() as f64 / 1e6);
        }
        return Ok(());
    }

    let mut total_bytes = 0usize;
    let t1 = Instant::now();

    if args.files.is_empty() {
        let stdin = std::io::stdin();
        for line in stdin.lock().lines() {
            let line = line?;
            total_bytes += line.len();
            write_line(&seg, &line, &args, &mut out)?;
        }
    } else {
        for path in &args.files {
            let file = std::fs::File::open(path).with_context(|| format!("開啟 {path}"))?;
            for line in std::io::BufReader::new(file).lines() {
                let line = line?;
                total_bytes += line.len();
                write_line(&seg, &line, &args, &mut out)?;
            }
        }
    }
    out.flush()?;

    if args.stats {
        print_resource_stats(&seg, &lexicon_report);
        let secs = t1.elapsed().as_secs_f64();
        eprintln!(
            "載入 {load_ms} ms; 處理 {:.2} MB / {:.3} s = {:.2} MB/s",
            total_bytes as f64 / 1e6,
            secs,
            total_bytes as f64 / 1e6 / secs.max(1e-9),
        );
    }
    Ok(())
}

fn read_all_text(files: &[String]) -> Result<String> {
    let mut text = String::new();
    if files.is_empty() {
        std::io::stdin().lock().read_to_string(&mut text)?;
    } else {
        for path in files {
            text.push_str(&std::fs::read_to_string(path).with_context(|| format!("讀取 {path}"))?);
            text.push('\n');
        }
    }
    Ok(text)
}

fn read_stopwords(path: Option<&str>) -> Result<Vec<String>> {
    let Some(path) = path else {
        return Ok(Vec::new());
    };
    let text = std::fs::read_to_string(path).with_context(|| format!("讀取停用詞 {path}"))?;
    Ok(text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(str::to_string)
        .collect())
}

fn write_summary_report(
    out: &mut impl Write,
    seg: &Segmenter,
    text: &str,
    top_k: usize,
    stopwords: Vec<String>,
    min_explainability: Option<f32>,
) -> Result<()> {
    use std::collections::{HashMap, HashSet};

    let started = Instant::now();
    let tokenizer = tiktoken_rs::o200k_base_singleton();
    let input_tokens = tokenizer.encode_with_special_tokens(text).len();
    let defaults = lingxi_core::SummaryOptions::default();
    let threshold = min_explainability.or(defaults.min_explainability);
    let preserve_structured_markdown = lingxi_core::should_preserve_structured_markdown(text);
    let summary = seg.extract_summary_with_options(
        text,
        top_k,
        &lingxi_core::SummaryOptions {
            stopwords: stopwords.clone(),
            min_explainability: threshold,
            ..defaults.clone()
        },
    );
    let diagnostics = seg.extract_summary_with_options(
        text,
        usize::MAX,
        &lingxi_core::SummaryOptions {
            min_sentence_chars: 1,
            stopwords,
            redundancy_threshold: None,
            min_explainability: Some(0.0),
            preserve_original_order: true,
            ..defaults
        },
    );
    let diagnostic_by_span: HashMap<(usize, usize), _> = diagnostics
        .iter()
        .map(|item| ((item.byte_start, item.byte_end), item))
        .collect();
    let selected_spans: HashSet<(usize, usize)> = summary
        .iter()
        .map(|item| (item.byte_start, item.byte_end))
        .collect();
    let summary_text = if preserve_structured_markdown {
        text.to_string()
    } else {
        summary
            .iter()
            .map(|item| item.text.as_str())
            .collect::<Vec<_>>()
            .join("\n")
    };
    let output_tokens = tokenizer.encode_with_special_tokens(&summary_text).len();
    let clauses = seg
        .split_clauses(text)
        .into_iter()
        .map(|clause| {
            let key = (clause.byte_start, clause.byte_end);
            let diagnostic = diagnostic_by_span.get(&key).copied();
            serde_json::json!({
                "text": clause.text,
                "byteStart": clause.byte_start,
                "byteEnd": clause.byte_end,
                "sentenceIndex": clause.sentence_index,
                "clauseIndex": clause.clause_index,
                "listItem": clause.list_item,
                "eligible": diagnostic.is_some(),
                "selected": selected_spans.contains(&key),
                "importance": diagnostic.map(|item| item.weight),
                "explainability": diagnostic.map(|item| item.explainability),
                "novelty": diagnostic.map(|item| item.novelty),
                "coverageGain": diagnostic.map(|item| item.coverage_gain),
                "signals": diagnostic.map(|item| serde_json::json!({
                    "properNounCount": item.signals.proper_noun_count,
                    "negationCount": item.signals.negation_count,
                    "emphasisCount": item.signals.emphasis_count,
                    "listItem": item.signals.list_item,
                    "objectNameCount": item.signals.object_name_count,
                    "dateCount": item.signals.date_count,
                    "numberCount": item.signals.number_count,
                    "quantityCount": item.signals.quantity_count,
                    "acronymCount": item.signals.acronym_count,
                })).unwrap_or_else(|| serde_json::json!({
                    "properNounCount": 0,
                    "negationCount": 0,
                    "emphasisCount": 0,
                    "listItem": clause.list_item,
                    "objectNameCount": 0,
                    "dateCount": 0,
                    "numberCount": 0,
                    "quantityCount": 0,
                    "acronymCount": 0,
                })),
            })
        })
        .collect::<Vec<_>>();
    let elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;
    let reduction_percent = if input_tokens == 0 {
        0.0
    } else {
        (1.0 - output_tokens as f64 / input_tokens as f64) * 100.0
    };
    let value = serde_json::json!({
        "engine": "lingxi-core",
        "mode": if preserve_structured_markdown {
            "structured-markdown-preserve-all"
        } else {
            "extractive-summary"
        },
        "llmCalls": 0,
        "tokenizer": "o200k_base (tiktoken)",
        "input": {
            "text": text,
            "tokens": input_tokens,
        },
        "output": {
            "text": summary_text,
            "tokens": output_tokens,
            "selectedClauses": summary.len(),
        },
        "settings": {
            "maxClauses": top_k,
            "minExplainability": threshold,
        },
        "clauseCount": clauses.len(),
        "reductionPercent": reduction_percent,
        "elapsedMs": elapsed_ms,
        "clauses": clauses,
    });
    serde_json::to_writer(&mut *out, &value)?;
    out.write_all(b"\n")?;
    Ok(())
}

fn print_resource_stats(seg: &Segmenter, lexicon_report: &str) {
    eprintln!("{lexicon_report}");
    let affect = seg.affect_stats();
    eprintln!(
        "情感詞 {}；各家族 {:?}；未知標籤 {}",
        affect.word_count, affect.family_counts, affect.unknown_label_count
    );
}

fn summarize_lexicons(specs: &[lingxi_core::CustomLexiconSpec]) -> String {
    let enabled: Vec<_> = specs.iter().filter(|spec| spec.enabled).collect();
    let domains: BTreeSet<_> = enabled.iter().map(|spec| spec.domain.as_str()).collect();
    let mut winners: HashMap<String, i8> = HashMap::new();
    let mut conflicts = 0usize;
    let mut overrides = 0usize;
    for spec in &enabled {
        for entry in &spec.entries {
            let normalized: String = entry
                .word
                .chars()
                .map(|character| {
                    let character = character.to_ascii_lowercase();
                    if character == '臺' {
                        '台'
                    } else {
                        character
                    }
                })
                .collect();
            match winners.get(&normalized).copied() {
                Some(priority) => {
                    conflicts += 1;
                    if spec.priority >= priority {
                        winners.insert(normalized, spec.priority);
                        overrides += 1;
                    }
                }
                None => {
                    winners.insert(normalized, spec.priority);
                }
            }
        }
    }
    format!(
        "結構化辭典 {} 份（啟用 {}）；領域 {:?}；衝突 {}；覆寫 {}；有效詞 {}",
        specs.len(),
        enabled.len(),
        domains,
        conflicts,
        overrides,
        winners.len()
    )
}

fn write_line(seg: &Segmenter, line: &str, args: &Args, out: &mut impl Write) -> Result<()> {
    match args.format {
        Format::Words => {
            let words = seg.cut(line);
            let mut first = true;
            for w in words {
                if w.trim().is_empty() {
                    continue; // 空白詞段對 words 格式沒有資訊量
                }
                if !first {
                    out.write_all(args.sep.as_bytes())?;
                }
                out.write_all(w.as_bytes())?;
                first = false;
            }
            out.write_all(b"\n")?;
        }
        Format::Tsv => {
            for t in seg.tokenize(line) {
                let w = &line[t.byte_start..t.byte_end];
                if w.trim().is_empty() {
                    continue;
                }
                writeln!(out, "{w}\t{}", seg.tag_name(t.tag))?;
            }
            out.write_all(b"\n")?;
        }
        Format::Jsonl => {
            // 手寫最小 JSON（詞內容需跳脫），避免多拉序列化依賴。
            out.write_all(br#"{"tokens":["#)?;
            let mut first = true;
            for t in seg.tokenize(line) {
                if !first {
                    out.write_all(b",")?;
                }
                let w = &line[t.byte_start..t.byte_end];
                write!(
                    out,
                    r#"{{"w":"{}","t":"{}","s":{},"e":{}}}"#,
                    json_escape(w),
                    seg.tag_name(t.tag),
                    t.byte_start,
                    t.byte_end
                )?;
                first = false;
            }
            out.write_all(b"]}\n")?;
        }
        Format::AnnotatedJson => {
            #[derive(Serialize)]
            struct OutputToken<'a> {
                w: &'a str,
                t: &'a str,
                s: usize,
                e: usize,
                #[serde(skip_serializing_if = "Option::is_none")]
                affect: Option<lingxi_core::AffectAnnotation>,
                #[serde(skip_serializing_if = "Option::is_none")]
                source: Option<lingxi_core::LexiconSource>,
            }
            #[derive(Serialize)]
            struct OutputLine<'a> {
                tokens: Vec<OutputToken<'a>>,
            }
            let tokens = seg
                .annotate(line)
                .into_iter()
                .map(|item| OutputToken {
                    w: &line[item.token.byte_start..item.token.byte_end],
                    t: seg.tag_name(item.token.tag),
                    s: item.token.byte_start,
                    e: item.token.byte_end,
                    affect: item.affect,
                    source: item.source,
                })
                .collect();
            serde_json::to_writer(&mut *out, &OutputLine { tokens })?;
            out.write_all(b"\n")?;
        }
    }
    Ok(())
}

/// 最小 JSON 字串跳脫。
fn json_escape(s: &str) -> String {
    let mut r = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => r.push_str("\\\""),
            '\\' => r.push_str("\\\\"),
            '\n' => r.push_str("\\n"),
            '\r' => r.push_str("\\r"),
            '\t' => r.push_str("\\t"),
            c if (c as u32) < 0x20 => r.push_str(&format!("\\u{:04x}", c as u32)),
            c => r.push(c),
        }
    }
    r
}
