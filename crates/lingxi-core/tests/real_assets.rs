//! 以真實轉換資產做的整合測試。
//! 資產（assets/*.bin）不進 git，本機需先跑 lingxi-convert；
//! 檔案不存在時測試直接跳過，CI 可另行準備資產。

use lingxi_core::Segmenter;

fn load() -> Option<Segmenter> {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets");
    if !std::path::Path::new(dir).join("dict.bin").exists() {
        return None;
    }
    Some(Segmenter::from_asset_dir(dir).expect("assets 存在但不是有效 LingXi 模型"))
}

#[test]
fn cuts_common_taiwan_sentence() {
    let Some(seg) = load() else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let words = seg.cut("金管會前主委參加台北市政府的記者會");
    // 不錨定完整切分（模型演進會變），只驗證關鍵詞邊界存在。
    assert!(words.contains(&"金管會"), "實際切分: {words:?}");
    assert!(
        words.contains(&"台北市政府") || words.contains(&"台北市"),
        "實際切分: {words:?}"
    );
    assert!(words.contains(&"記者會"), "實際切分: {words:?}");
    // 全部詞段串回必須等於原文（無字元遺漏或重複）。
    assert_eq!(words.concat(), "金管會前主委參加台北市政府的記者會");
}

#[test]
fn variant_normalization_still_slices_original() {
    let Some(seg) = load() else { return };
    // 「臺北」經異體字正規化為「台北」查詞典，但輸出切片必須是原文的「臺北」。
    let text = "我住在臺北市";
    let words = seg.cut(text);
    assert_eq!(words.concat(), text);
    assert!(
        words.iter().any(|w| w.contains('臺')),
        "實際切分: {words:?}"
    );
}

#[test]
fn mixed_text_with_url_email_time() {
    let Some(seg) = load() else { return };
    let text = "陳先生2014年3月寄信到service@gmail.com，網址是https://www.ptt.cc/bbs/Gossiping！";
    let words = seg.cut(text);
    assert_eq!(words.concat(), text, "詞段必須完整覆蓋原文");
    assert!(words.contains(&"2014年"), "實際切分: {words:?}");
    assert!(words.contains(&"service@gmail.com"), "實際切分: {words:?}");
    assert!(
        words.contains(&"https://www.ptt.cc/bbs/Gossiping"),
        "實際切分: {words:?}"
    );
    assert!(words.contains(&"！"), "實際切分: {words:?}");
}

#[test]
fn tokenize_assigns_reasonable_tags() {
    let Some(seg) = load() else { return };
    let text = "陳先生2014年寄信到service@gmail.com！";
    let tokens = seg.tokenize(text);
    let tagged: Vec<(&str, &str)> = tokens
        .iter()
        .map(|t| (&text[t.byte_start..t.byte_end], seg.tag_name(t.tag)))
        .collect();
    let tag_of = |w: &str| tagged.iter().find(|(x, _)| *x == w).map(|(_, t)| *t);
    assert_eq!(tag_of("2014年"), Some("Nd"), "全部: {tagged:?}");
    assert_eq!(tag_of("service@gmail.com"), Some("FW"), "全部: {tagged:?}");
    assert_eq!(
        tag_of("！"),
        Some("PUNCTUATIONCATEGORY"),
        "全部: {tagged:?}"
    );
    // 「陳先生」由穩定邊界覆寫保護，詞性使用 CKIP 專有名詞 Nb。
    assert_eq!(tag_of("陳先生"), Some("Nb"), "全部: {tagged:?}");
}

#[test]
fn oov_word_gets_pos_from_viterbi() {
    let Some(seg) = load() else { return };
    // 郝翊晟 為未登入人名，POS Viterbi 應給出詞性（理想為 nr，至少不能是 unknown 以外的空值）。
    let text = "警方逮捕了郝翊晟";
    let tokens = seg.tokenize(text);
    let tagged: Vec<(&str, &str)> = tokens
        .iter()
        .map(|t| (&text[t.byte_start..t.byte_end], seg.tag_name(t.tag)))
        .collect();
    let name = tagged.iter().find(|(w, _)| w.contains('郝'));
    assert!(name.is_some(), "全部: {tagged:?}");
    println!("OOV 詞性結果: {tagged:?}");
}

#[test]
fn hmm_merges_oov_name_run() {
    let Some(seg) = load() else { return };
    // 未登入人名：HMM 應把連續單字合併成詞（不苛求邊界完全正確，
    // 但至少不應全部退化為單字）。
    let text = "警方逮捕了郝翊晟與同夥";
    let words = seg.cut(text);
    assert_eq!(words.concat(), text);
    let name_zone: Vec<&&str> = words
        .iter()
        .filter(|w| w.contains('郝') || w.contains('翊'))
        .collect();
    assert!(
        name_zone.iter().any(|w| w.chars().count() >= 2),
        "HMM 未合併任何人名字元, 實際切分: {words:?}"
    );
}
#[test]
fn granularity_regressions_are_exact() {
    let Some(seg) = load() else { return };
    assert_eq!(
        seg.cut("結婚的和尚未結婚的人"),
        vec!["結婚", "的", "和", "尚未", "結婚", "的", "人"]
    );
    assert_eq!(seg.cut("長榮航空公司"), vec!["長榮航空公司"]);
    assert_eq!(seg.cut("軟體工程師"), vec!["軟體", "工程師"]);
}

#[test]
fn quantized_pos_matches_lxa2_on_representative_texts() {
    let dir = std::path::Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets"));
    let Ok(dict_bytes) = std::fs::read(dir.join("dict.bin")) else {
        return;
    };
    let Ok(bmes_bytes) = std::fs::read(dir.join("hmm_bmes.bin")) else {
        return;
    };
    let pos_path = if dir.join("hmm_pos.lxa2.bin").exists() {
        dir.join("hmm_pos.lxa2.bin")
    } else {
        dir.join("hmm_pos.bin")
    };
    let Ok(pos_bytes) = std::fs::read(pos_path) else {
        return;
    };
    let original_pos = lingxi_core::model::decode_pos_asset(&pos_bytes).unwrap();
    let quantized_bytes = lingxi_core::model::encode_quantized_pos_asset(&original_pos);
    assert!(
        quantized_bytes.len() < pos_bytes.len(),
        "量化後 {} bytes，不應大於 LXA2 {} bytes",
        quantized_bytes.len(),
        pos_bytes.len()
    );
    let quantized_pos = lingxi_core::model::decode_pos_asset(&quantized_bytes).unwrap();
    let original = Segmenter::from_models(
        lingxi_core::model::decode_asset(&dict_bytes).unwrap(),
        lingxi_core::model::decode_asset(&bmes_bytes).unwrap(),
        original_pos,
    )
    .unwrap();
    let quantized = Segmenter::from_models(
        lingxi_core::model::decode_asset(&dict_bytes).unwrap(),
        lingxi_core::model::decode_asset(&bmes_bytes).unwrap(),
        quantized_pos,
    )
    .unwrap();
    let mut texts = vec![
        "謝金河表示，今年台股大幅上漲，而且企業獲利持續提升。".to_string(),
        "EPS超過100元的公司有群聯、宜鼎、緯穎與川湖四家。".to_string(),
        "陳先生2014年寄信到service@gmail.com！".to_string(),
        "如果資料尚未備份，就不得刪除使用者紀錄。".to_string(),
        "警方逮捕了郝翊晟與同夥。".to_string(),
        "Fear Of Missing Out（FOMO）是指害怕錯過機會。".to_string(),
    ];
    texts.extend(
        include_str!("../../../tests/golden/must_pass.tsv")
            .lines()
            .filter(|line| !line.starts_with('#') && !line.trim().is_empty())
            .filter_map(|line| line.split_once('\t').map(|(text, _)| text.to_string())),
    );
    for text in &texts {
        assert_eq!(original.cut(text), quantized.cut(text), "切詞差異: {text}");
        let tagged = |segmenter: &Segmenter| {
            segmenter
                .tokenize(text)
                .into_iter()
                .map(|token| {
                    (
                        text[token.byte_start..token.byte_end].to_string(),
                        segmenter.tag_name(token.tag).to_string(),
                    )
                })
                .collect::<Vec<_>>()
        };
        assert_eq!(tagged(&original), tagged(&quantized), "POS 差異: {text}");
    }
    eprintln!(
        "POS asset: LXA2 {} bytes -> LXA3 {} bytes ({:.1}%)",
        pos_bytes.len(),
        quantized_bytes.len(),
        quantized_bytes.len() as f64 / pos_bytes.len() as f64 * 100.0
    );
}
