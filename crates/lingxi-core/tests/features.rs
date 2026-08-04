//! 自訂詞典與 TextRank 的真實資產整合測試。
//! 資產不存在時跳過（同 golden.rs 慣例）。

use lingxi_core::{parse_user_dict, KeywordOptions, Segmenter};

const ASSET_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets");

#[test]
fn user_dict_fixes_known_boundary_cases() {
    // 已知邊界案例：柯文哲（柯文是詞典詞）、板南線（缺詞）。
    let entries = parse_user_dict("柯文哲 100000 Nb\n板南線 100000 Nb\n");
    let Ok(seg) = Segmenter::from_asset_dir_with_user_dict(ASSET_DIR, &entries) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let words = seg.cut("柯文哲搭板南線上班");
    assert!(words.contains(&"柯文哲"), "缺「柯文哲」，實際: {words:?}");
    assert!(words.contains(&"板南線"), "缺「板南線」，實際: {words:?}");
    // 自訂詞性應可經 tokenize 取回。
    let tokens = seg.tokenize("柯文哲搭板南線上班");
    let tags: Vec<&str> = tokens.iter().map(|t| seg.tag_name(t.tag)).collect();
    assert!(
        tags.iter().filter(|&&tag| tag == "Nb").count() >= 2,
        "自訂詞應為 CKIP Nb，實際: {tags:?}"
    );
}

#[test]
fn user_dict_does_not_disturb_unrelated_text() {
    let entries = parse_user_dict("板南線 100000 Nb\n");
    let (Ok(base), Ok(with_user)) = (
        Segmenter::from_asset_dir(ASSET_DIR),
        Segmenter::from_asset_dir_with_user_dict(ASSET_DIR, &entries),
    ) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    // 不含自訂詞的句子，切分結果應與無自訂詞典時完全一致。
    let text = "行政院會今天通過中央政府總預算案，將送立法院審議。";
    assert_eq!(base.cut(text), with_user.cut(text));
}

#[test]
fn mixed_script_words_work_in_curated_and_user_dicts() {
    let entries = parse_user_dict("COVID疫苗 100000 Na\n3D列印 100000 Na\nCheryl姐 100000 Nb\n");
    let Ok(seg) = Segmenter::from_asset_dir_with_user_dict(ASSET_DIR, &entries) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "看AV女優用PDF檔，COVID疫苗配合3D列印，Cheryl姐來了";
    let words = seg.cut(text);
    for expected in ["AV女優", "PDF檔", "COVID疫苗", "3D列印", "Cheryl姐"] {
        assert!(
            words.contains(&expected),
            "缺「{expected}」，實際: {words:?}"
        );
    }
    assert_eq!(words.concat(), text);
}

#[test]
fn textrank_extracts_reasonable_keywords() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "行政院今天召開院會，討論中央政府總預算案。行政院長表示，\
                總預算案將優先投入社會福利與國防預算，並強化半導體產業的供應鏈韌性。\
                立法院預計下週開始審議總預算案，朝野立委對國防預算的編列仍有歧見。";
    let kws = seg.extract_keywords(text, 10);
    assert!(!kws.is_empty(), "關鍵字不應為空");
    // 權重應降冪排列。
    for w in kws.windows(2) {
        assert!(w[0].weight >= w[1].weight, "權重未降冪: {kws:?}");
    }
    // 高頻核心詞應入榜。
    let words: Vec<&str> = kws.iter().map(|k| k.word.as_str()).collect();
    assert!(
        words.iter().any(|w| w.contains("預算")),
        "「預算」相關詞應入榜，實際: {words:?}"
    );
    // 候選過濾：不應出現單字詞或標點。
    assert!(
        words.iter().all(|w| w.chars().count() >= 2),
        "出現單字詞: {words:?}"
    );
}

#[test]
fn textrank_empty_and_no_candidate_inputs() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    assert!(seg.extract_keywords("", 10).is_empty());
    assert!(seg.extract_keywords("，。！？", 10).is_empty());
}

#[test]
fn proper_noun_channel_is_independent_and_can_be_disabled() {
    let entries = parse_user_dict("亞特蘭提斯 1000000000 Nb\n");
    let Ok(seg) = Segmenter::from_asset_dir_with_user_dict(ASSET_DIR, &entries) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "亞特蘭提斯宣布推動海洋研究";
    let entity_enabled =
        seg.extract_keywords_with_options(text, 10, Some(&["VC"]), KeywordOptions::default());
    assert!(
        entity_enabled
            .iter()
            .any(|keyword| keyword.word == "亞特蘭提斯"),
        "Nb 不在一般白名單時仍應能由專有名詞通道進榜: {entity_enabled:?}"
    );

    let entity_disabled = seg.extract_keywords_with_options(
        text,
        10,
        Some(&["VC"]),
        KeywordOptions {
            proper_noun_enabled: false,
            ..KeywordOptions::default()
        },
    );
    assert!(
        entity_disabled
            .iter()
            .all(|keyword| keyword.word != "亞特蘭提斯"),
        "停用專有名詞通道後應完全遵循一般詞性白名單: {entity_disabled:?}"
    );
}
