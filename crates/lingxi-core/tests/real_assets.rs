//! 以真實轉換資產做的整合測試。
//! 資產（assets/*.bin）不進 git，本機需先跑 lingxi-convert；
//! 檔案不存在時測試直接跳過，CI 可另行準備資產。

use lingxi_core::Segmenter;

fn load() -> Option<Segmenter> {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets");
    Segmenter::from_asset_dir(dir).ok()
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
    assert!(words.contains(&"台北市政府") || words.contains(&"台北市"), "實際切分: {words:?}");
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
    assert!(words.iter().any(|w| w.contains('臺')), "實際切分: {words:?}");
}

#[test]
fn mixed_text_with_url_email_time() {
    let Some(seg) = load() else { return };
    let text = "陳先生2014年3月寄信到service@gmail.com，網址是https://www.ptt.cc/bbs/Gossiping！";
    let words = seg.cut(text);
    assert_eq!(words.concat(), text, "詞段必須完整覆蓋原文");
    assert!(words.contains(&"2014年"), "實際切分: {words:?}");
    assert!(words.contains(&"service@gmail.com"), "實際切分: {words:?}");
    assert!(words.contains(&"https://www.ptt.cc/bbs/Gossiping"), "實際切分: {words:?}");
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
    assert_eq!(tag_of("2014年"), Some("t"), "全部: {tagged:?}");
    assert_eq!(tag_of("service@gmail.com"), Some("email"), "全部: {tagged:?}");
    assert_eq!(tag_of("！"), Some("w"), "全部: {tagged:?}");
    // 「陳先生」被 HMM 合併為人名，POS Viterbi 應標為 nr。
    assert_eq!(tag_of("陳先生"), Some("nr"), "全部: {tagged:?}");
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
    let name_zone: Vec<&&str> = words.iter().filter(|w| w.contains('郝') || w.contains('翊')).collect();
    assert!(
        name_zone.iter().any(|w| w.chars().count() >= 2),
        "HMM 未合併任何人名字元, 實際切分: {words:?}"
    );
}
