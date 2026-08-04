//! 黃金集驗證：
//! 1. must_pass.tsv — 人工整理的關鍵詞邊界斷言（包含式，非完整切分比對）
//! 2. 真實語料結構驗證 — 對 ModelingData 新聞語料抽樣，驗證覆蓋不變量
//!    （詞段無縫拼回原文）並輸出分佈統計供人工檢視。
//!
//! 資產或語料不存在時測試跳過（CI 需先跑 lingxi-convert）。

use lingxi_core::Segmenter;

fn load() -> Option<Segmenter> {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets");
    if !std::path::Path::new(dir).join("dict.bin").exists() {
        return None;
    }
    Some(Segmenter::from_asset_dir(dir).expect("assets 存在但不是有效 LXA2 模型"))
}

#[test]
fn must_pass_word_boundaries() {
    let Some(seg) = load() else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let tsv = include_str!("../../../tests/golden/must_pass.tsv");
    let mut failures: Vec<String> = Vec::new();
    let mut checked = 0usize;
    for line in tsv.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((sentence, expected)) = line.split_once('\t') else {
            failures.push(format!("格式錯誤: {line}"));
            continue;
        };
        let words = seg.cut(sentence);
        assert_eq!(words.concat(), sentence, "覆蓋不變量失敗: {sentence}");
        for want in expected.split('|') {
            checked += 1;
            if !words.contains(&want) {
                failures.push(format!("「{sentence}」缺少「{want}」，實際: {words:?}"));
            }
        }
    }
    let pass_rate = 100.0 * (checked - failures.len()) as f64 / checked as f64;
    eprintln!(
        "must-pass: {}/{} ({pass_rate:.1}%)",
        checked - failures.len(),
        checked
    );
    assert!(
        failures.is_empty(),
        "{} 項失敗:\n{}",
        failures.len(),
        failures.join("\n")
    );
}

#[test]
fn corpus_coverage_and_stats() {
    let Some(seg) = load() else { return };
    // 語料為本機舊專案資料，不隨 repo 散布；不存在即跳過。
    let corpus_paths = [
        r"D:\PycharmProjects\LingXi\ModelingData\NewsData.txt",
        r"D:\PycharmProjects\LingXi\ModelingData\UncutNewsData.txt",
    ];
    let Some(path) = corpus_paths
        .iter()
        .find(|p| std::path::Path::new(p).exists())
    else {
        eprintln!("語料不存在，跳過");
        return;
    };
    let content = std::fs::read_to_string(path).expect("讀取語料");
    let mut lines_checked = 0usize;
    let mut total_words = 0usize;
    let mut single_char_words = 0usize;
    let mut total_chars = 0usize;
    for line in content.lines().take(500) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let words = seg.cut(line);
        // 不變量：詞段無縫拼回原文（無遺漏、無重複、無 panic）。
        assert_eq!(words.concat(), line, "覆蓋不變量失敗於: {line}");
        lines_checked += 1;
        for w in &words {
            let n = w.chars().count();
            if w.trim().is_empty() || n == 0 {
                continue;
            }
            total_words += 1;
            total_chars += n;
            if n == 1 {
                single_char_words += 1;
            }
        }
    }
    assert!(lines_checked > 0, "語料無有效行");
    eprintln!(
        "語料驗證: {} 行, {} 詞, 平均詞長 {:.2} 字, 單字詞比例 {:.1}%",
        lines_checked,
        total_words,
        total_chars as f64 / total_words as f64,
        100.0 * single_char_words as f64 / total_words as f64,
    );
    // 粗略健全性界線：中文新聞語料平均詞長應落在 1.2–3.0 字之間，
    // 單字詞比例不應超過六成（否則代表詞典或 HMM 管線壞掉）。
    let avg = total_chars as f64 / total_words as f64;
    assert!((1.2..=3.0).contains(&avg), "平均詞長異常: {avg:.2}");
    assert!(
        (single_char_words as f64) < 0.6 * total_words as f64,
        "單字詞比例異常偏高"
    );
}
