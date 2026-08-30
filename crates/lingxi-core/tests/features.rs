//! 自訂詞典與 TextRank 的真實資產整合測試。
//! 資產不存在時跳過（同 golden.rs 慣例）。

use lingxi_core::{
    parse_user_dict, KeywordExtractionOptions, KeywordOptions, Segmenter, SummaryDecision,
    SummaryOptions,
};

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
fn configurable_keywords_honor_stopwords() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "行政院討論總預算案，立法院將審議總預算案與國防預算。";
    let baseline = seg.extract_keywords(text, 10);
    let Some(first) = baseline.first() else {
        panic!("測試文本應產生關鍵字");
    };
    let configured = seg.extract_keywords_configured(
        text,
        10,
        None,
        &KeywordExtractionOptions {
            stopwords: vec![first.word.clone()],
            ..KeywordExtractionOptions::default()
        },
    );
    assert!(configured.iter().all(|keyword| keyword.word != first.word));
}

#[test]
fn summary_document_preserves_original_block_offsets() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "行政院今天通過中央政府總預算案。\n\n立法院下週將開始審議總預算案。\n\n氣象署表示颱風目前距離台灣很遠。";
    let summary = seg.extract_summary_with_options(
        text,
        2,
        &SummaryOptions {
            min_sentence_chars: 4,
            ..SummaryOptions::default()
        },
    );
    assert_eq!(summary.schema_version, 2);
    assert!(summary.budget.selected_ranked_blocks <= 2);
    for block in &summary.blocks {
        assert_eq!(&text[block.byte_start..block.byte_end], block.source_text);
        if let Some(score) = &block.score {
            assert!(score.final_score.is_finite());
        }
    }
}

#[test]
fn summary_uses_block_gate_and_reports_signals() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "部署流程已完成，但 **不得** 呼叫 `tools.delete_all()`；一般背景資料仍持續更新。\n\n其他團隊預計下週檢視結果。";
    let explained = seg.extract_summary_with_options(
        text,
        10,
        &SummaryOptions {
            min_sentence_chars: 4,
            min_explainability: Some(0.0),
            ..SummaryOptions::default()
        },
    );
    let protected = explained
        .blocks
        .iter()
        .find(|item| item.source_text.contains("delete_all"))
        .expect("含否定、強調與工具名的 block 應存在");
    assert!(protected.signals.negation_count > 0);
    assert!(protected.signals.emphasis_count > 0);
    assert!(protected.signals.object_name_count > 0);
    assert_ne!(protected.decision, SummaryDecision::Omit);

    let strict = seg.extract_summary_with_options(
        text,
        10,
        &SummaryOptions {
            min_sentence_chars: 4,
            min_explainability: Some(0.99),
            ..SummaryOptions::default()
        },
    );
    assert!(
        strict.text.is_empty(),
        "低於門檻者一律不納入，也不得保底回填"
    );
}

#[test]
fn keyphrases_are_adjacent_original_spans() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "中央政府總預算案送交立法院，中央政府總預算案將優先審議。";
    let phrases = seg.extract_keyphrases(text, 10);
    assert!(!phrases.is_empty(), "應能由相鄰關鍵詞形成短語");
    for phrase in phrases {
        assert!(phrase.occurrences >= 1);
        assert!(phrase
            .spans
            .iter()
            .all(|span| text.get(span.byte_start..span.byte_end) == Some(phrase.phrase.as_str())));
    }
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

#[test]
fn structured_lexicon_is_frequency_free_and_affect_is_multi_label() {
    let read_model = |name: &str| std::fs::read(format!("{ASSET_DIR}/{name}"));
    let (Ok(dict), Ok(bmes), Ok(pos)) = (
        read_model("dict.bin"),
        read_model("hmm_bmes.bin"),
        read_model("hmm_pos.bin"),
    ) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let taxonomy = lingxi_core::parse_taxonomy(include_str!(
        "../../../resources/affect/emotion-taxonomy.json"
    ))
    .unwrap();
    let lexicon = lingxi_core::parse_affect_lexicon(include_str!(
        "../../../resources/affect/emotion-lexicon.json"
    ))
    .unwrap();
    let affect = lingxi_core::build_affect_model(taxonomy, lexicon).unwrap();
    let affect_bytes = lingxi_core::model::encode_asset(&affect);
    let affect: lingxi_core::AffectModel =
        lingxi_core::model::decode_asset(&affect_bytes).expect("affect.bin 應可 round-trip");
    let custom = lingxi_core::parse_custom_lexicon(
        r#"{
          "schemaVersion": 1,
          "id": "medical-tw",
          "domain": "medical",
          "priority": 3,
          "entries": [{"word": "板南線", "pos": "Na"}, {"word": "A肝", "pos": "Na"}]
        }"#,
    )
    .unwrap();
    let seg = Segmenter::from_models_with_options(
        lingxi_core::model::decode_asset(&dict).unwrap(),
        lingxi_core::model::decode_asset(&bmes).unwrap(),
        lingxi_core::model::decode_pos_asset(&pos).unwrap(),
        Some(affect),
        lingxi_core::SegmenterOptions {
            custom_lexicons: vec![custom],
        },
    )
    .unwrap();

    let baseline = Segmenter::from_asset_dir(ASSET_DIR).unwrap();
    let unrelated = "行政院公布最新經濟成長率";
    assert_eq!(
        seg.cut(unrelated),
        baseline.cut(unrelated),
        "不含自訂詞的句子切分必須不變"
    );

    let words = seg.cut("搭板南線後感到欣慰");
    assert!(words.contains(&"板南線"), "{words:?}");
    assert!(seg.cut("A肝研究").contains(&"A肝"));
    let custom_annotation = seg
        .annotate("搭板南線")
        .into_iter()
        .find(|item| &"搭板南線"[item.token.byte_start..item.token.byte_end] == "板南線")
        .expect("板南線應維持完整詞界");
    let source = custom_annotation.source.expect("應保留自訂辭典來源");
    assert_eq!(source.id, "medical-tw");
    assert_eq!(source.domain, "medical");
    assert_eq!(source.priority, 3);

    let annotations = seg.annotate("感到欣慰");
    let item = annotations
        .iter()
        .find(|item| &"感到欣慰"[item.token.byte_start..item.token.byte_end] == "欣慰")
        .expect("欣慰應維持完整詞界");
    let affect = item.affect.as_ref().expect("欣慰應有情感標註");
    assert_eq!(affect.emotions, ["joy.joy", "joy.relief"]);
    assert_eq!(affect.polarity, lingxi_core::Polarity::Positive);
}

#[test]
fn structural_blocks_are_preserved_outside_ranked_budget() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "# 重點\n\n一般背景。\n\n```rs\nfn main() {}\n```\n\n1. 第一項。\n2. 第二項。\n\n| A | B |\n|---|---|\n| 1 | 2 |";
    let summary = seg.extract_summary_with_options(text, 0, &SummaryOptions::default());
    assert!(summary.text.contains("fn main() {}"));
    assert!(summary.text.contains("1. 第一項。"));
    assert!(summary.text.contains("2. 第二項。"));
    assert!(summary.text.contains("| A | B |"));
    assert!(!summary.text.contains("一般背景。"));
    assert!(summary.budget.preserved_blocks >= 4);
}

#[test]
fn ranked_blocks_obey_max_blocks_and_soft_facts_do_not_override_conclusion() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = "版權頁記載本書出版於2024年。\n\n作者曾在2019年搬家。\n\n研究核心發現是睡眠品質與記憶鞏固密切相關。";
    let summary = seg.extract_summary(text, 1);
    assert_eq!(summary.budget.selected_ranked_blocks, 1);
    assert!(summary.text.contains("研究核心發現"), "{summary:?}");
    assert!(!summary.text.contains("2024年"));
}

#[test]
fn long_selected_block_forces_valid_negation_clauses() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = format!(
        "{}。{}。不得把原文傳到外部服務。{}。不能刪除使用者資料。",
        "背景資訊".repeat(90),
        "系統已完成第一階段分析".repeat(20),
        "其他說明".repeat(40),
    );
    let summary = seg.extract_summary_with_options(
        &text,
        1,
        &SummaryOptions {
            min_explainability: Some(0.0),
            max_clauses_per_long_block: 1,
            ..SummaryOptions::default()
        },
    );
    assert!(summary.text.contains("不得把原文傳到外部服務"));
    assert!(summary.text.contains("不能刪除使用者資料"));
    assert!(summary.budget.forced_negation_clauses >= 1);
}

#[test]
fn long_list_item_summarizes_prose_but_keeps_marker_and_negation() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let text = format!(
        "- {}。第一階段已完成。不得刪除使用者資料。最後才進行清理。",
        "背景說明".repeat(70)
    );
    let summary = seg.extract_summary_with_options(
        &text,
        0,
        &SummaryOptions {
            max_clauses_per_long_list_item: 1,
            ..SummaryOptions::default()
        },
    );
    let block = &summary.blocks[0];
    assert_eq!(
        block.decision,
        lingxi_core::SummaryDecision::SummarizeWithin
    );
    assert!(block.output_text.starts_with("- "));
    assert!(block.output_text.contains("不得刪除使用者資料"));
    assert!(block.output_text.len() < block.source_text.len());
}

#[test]
fn summary_v2_matches_cross_runtime_golden_contract() {
    let Ok(seg) = Segmenter::from_asset_dir(ASSET_DIR) else {
        eprintln!("assets 不存在，跳過");
        return;
    };
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("../../../tests/golden/summary-v2.json")).unwrap();
    let input = fixture["input"].as_str().unwrap();
    let max_blocks = fixture["maxBlocks"].as_u64().unwrap() as usize;
    let summary = seg.extract_summary_with_options(
        input,
        max_blocks,
        &SummaryOptions {
            min_explainability: Some(0.0),
            ..SummaryOptions::default()
        },
    );
    assert_eq!(summary.text, fixture["expectedSummary"].as_str().unwrap());
    let kinds: Vec<_> = summary
        .blocks
        .iter()
        .map(|block| serde_json::to_value(block.kind).unwrap())
        .collect();
    let decisions: Vec<_> = summary
        .blocks
        .iter()
        .map(|block| serde_json::to_value(block.decision).unwrap())
        .collect();
    let negations: Vec<_> = summary
        .blocks
        .iter()
        .map(|block| block.signals.negation_count)
        .collect();
    let money: Vec<_> = summary
        .blocks
        .iter()
        .map(|block| block.signals.money_count)
        .collect();
    assert_eq!(kinds, fixture["expectedKinds"].as_array().unwrap().clone());
    assert_eq!(
        decisions,
        fixture["expectedDecisions"].as_array().unwrap().clone()
    );
    assert_eq!(negations, vec![0, 1, 0, 0]);
    assert_eq!(money, vec![0, 0, 0, 1]);
}
