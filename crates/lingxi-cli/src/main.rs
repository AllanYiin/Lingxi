//! lingxi CLI：逐行讀 stdin 或檔案，輸出分詞結果。
//!
//! 用法：
//!   lingxi [--assets DIR] [--user-dict FILE] [--format words|tsv|jsonl]
//!          [--sep 分隔符] [--keywords N] [FILE...]
//!
//! - words（預設）：一行輸入一行輸出，詞以 --sep（預設 "/"）連接，空白詞段略過
//! - tsv：每詞一行「詞\t詞性」，輸入行之間以空行分隔
//! - jsonl：每行輸入輸出一行 JSON：{"tokens":[{"w":..,"t":..,"s":..,"e":..}]}
//! - --keywords N：改為對全部輸入做 TextRank，輸出 N 行「詞\t權重」後結束
//! - --user-dict：jieba 格式自訂詞典（每行 `詞 [頻率] [詞性]`）
//!
//! 資產目錄搜尋順序：--assets → 環境變數 LINGXI_ASSETS → ./assets。

use std::io::{BufRead, BufWriter, Read, Write};
use std::time::Instant;

use anyhow::{bail, Context, Result};
use lingxi_core::Segmenter;

enum Format {
    Words,
    Tsv,
    Jsonl,
}

struct Args {
    assets: String,
    user_dict: Option<String>,
    format: Format,
    sep: String,
    files: Vec<String>,
    stats: bool,
    /// Some(n) = 關鍵字模式：全部輸入做 TextRank，取前 n 個。
    keywords: Option<usize>,
}

fn parse_args() -> Result<Args> {
    let mut args = Args {
        assets: std::env::var("LINGXI_ASSETS").unwrap_or_else(|_| "assets".into()),
        user_dict: None,
        format: Format::Words,
        sep: "/".into(),
        files: Vec::new(),
        stats: false,
        keywords: None,
    };
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--assets" => args.assets = it.next().context("--assets 需要參數")?,
            "--user-dict" => args.user_dict = Some(it.next().context("--user-dict 需要參數")?),
            "--sep" => args.sep = it.next().context("--sep 需要參數")?,
            "--format" => {
                args.format = match it.next().context("--format 需要參數")?.as_str() {
                    "words" => Format::Words,
                    "tsv" => Format::Tsv,
                    "jsonl" => Format::Jsonl,
                    other => bail!("未知格式 {other}（可用 words|tsv|jsonl）"),
                }
            }
            "--keywords" => {
                args.keywords = Some(
                    it.next().context("--keywords 需要參數")?.parse().context("--keywords 需為整數")?,
                )
            }
            "--stats" => args.stats = true,
            "--help" | "-h" => {
                eprintln!("用法: lingxi [--assets DIR] [--user-dict FILE] [--format words|tsv|jsonl] [--sep S] [--keywords N] [--stats] [FILE...]");
                std::process::exit(0);
            }
            _ => args.files.push(a),
        }
    }
    Ok(args)
}

fn main() -> Result<()> {
    let args = parse_args()?;

    let t0 = Instant::now();
    let user_entries = match &args.user_dict {
        Some(path) => {
            let text = std::fs::read_to_string(path).with_context(|| format!("讀取自訂詞典 {path}"))?;
            lingxi_core::parse_user_dict(&text)
        }
        None => Vec::new(),
    };
    let seg = Segmenter::from_asset_dir_with_user_dict(&args.assets, &user_entries)
        .with_context(|| format!("載入資產目錄 {} 失敗（可用 --assets 或 LINGXI_ASSETS 指定）", args.assets))?;
    let load_ms = t0.elapsed().as_millis();

    let stdout = std::io::stdout();
    let mut out = BufWriter::new(stdout.lock());

    // 關鍵字模式：整份輸入一次抽取，與逐行分詞管線互斥。
    if let Some(top_k) = args.keywords {
        let mut text = String::new();
        if args.files.is_empty() {
            std::io::stdin().lock().read_to_string(&mut text)?;
        } else {
            for path in &args.files {
                text.push_str(
                    &std::fs::read_to_string(path).with_context(|| format!("讀取 {path}"))?,
                );
                text.push('\n');
            }
        }
        for k in seg.extract_keywords(&text, top_k) {
            writeln!(out, "{}\t{:.4}", k.word, k.weight)?;
        }
        out.flush()?;
        if args.stats {
            eprintln!("載入 {load_ms} ms; 抽取 {:.2} MB", text.len() as f64 / 1e6);
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
