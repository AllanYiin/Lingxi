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
