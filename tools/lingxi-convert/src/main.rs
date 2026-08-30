//! lingxi-convert：將舊版 LingXi 的 JSON 模型資產轉為 postcard 二進位。
//!
//! 用法（參數皆可省略，預設對應本 repo 的相對位置）：
//!   lingxi-convert [model_dir] [out_dir] [affect_source_dir]
//!   lingxi-convert --quantize-pos input_hmm_pos.bin output_hmm_pos.bin
//!
//! 輸出：out_dir/dict.bin、hmm_bmes.bin、hmm_pos.bin，並列印轉換統計
//! 與機率 spot-check 供人工對照 JSON 原值。

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use daachorse::CharwiseDoubleArrayAhoCorasick;
use serde_json::Value;

use lingxi_core::model::{
    decode_pos_asset, encode_asset, encode_quantized_pos_asset, BmesModel, CharTable, DictModel,
    PosModel, MIN_LOG,
};
use lingxi_core::{build_affect_model, parse_affect_lexicon, parse_taxonomy};

/// BMES 狀態固定順序，與 lingxi_core::model 的 STATE_* 對齊。
const STATES: [&str; 4] = ["B", "M", "E", "S"];
const MIN_DICTIONARY_FREQUENCY: f64 = 10.0;

const MODEL_FINGERPRINT_FILES: &[&str] = &[
    "Dict.json",
    "VariantWords.json",
    "startProbs.json",
    "transProbs.json",
    "transProbs2.json",
    "emmitProbs.json",
    "emmitProbs2.json",
    "tagStartProbs.json",
    "tagTransProbs.json",
    "tagTransProbs2.json",
    "tagEmitProbs.json",
    "PosLexicon.json",
];

fn validate_model_fingerprint(model_dir: &Path) -> Result<String> {
    let mut value = 0xcbf29ce484222325u64;
    for name in MODEL_FINGERPRINT_FILES {
        let mut feed = |byte: u8| {
            value ^= byte as u64;
            value = value.wrapping_mul(0x100000001b3);
        };
        for &byte in name.as_bytes() {
            feed(byte);
        }
        feed(0);
        for byte in fs::read(model_dir.join(name))
            .with_context(|| format!("讀取 canonical model file {name}"))?
        {
            feed(byte);
        }
    }
    let actual = format!("fnv1a64:{value:016x}");
    let expected = fs::read_to_string(model_dir.join("model-fingerprint.txt"))
        .context("讀取 model-fingerprint.txt")?;
    if expected.trim() != actual {
        bail!(
            "canonical model fingerprint 不符：expected {}, actual {actual}",
            expected.trim()
        );
    }
    Ok(actual)
}
fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().is_some_and(|value| value == "--quantize-pos") {
        if args.len() != 3 {
            bail!("用法: lingxi-convert --quantize-pos input_hmm_pos.bin output_hmm_pos.bin");
        }
        let input = fs::read(&args[1]).with_context(|| format!("讀取 {}", args[1]))?;
        let model = decode_pos_asset(&input).context("解碼 POS asset")?;
        let output = encode_quantized_pos_asset(&model);
        fs::write(&args[2], &output).with_context(|| format!("寫入 {}", args[2]))?;
        println!(
            "[pos-quantize] {} -> {} bytes ({:.1}%)",
            input.len(),
            output.len(),
            output.len() as f64 / input.len().max(1) as f64 * 100.0
        );
        return Ok(());
    }
    let model_dir = args
        .first()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".corpus-work/legacy-ckip-canonical-v3/model"));
    let out_dir = args
        .get(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("assets"));
    let affect_dir = args
        .get(2)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("resources/affect"));
    fs::create_dir_all(&out_dir)?;

    println!("model: {}", model_dir.display());
    println!("out  : {}\n", out_dir.display());
    let fingerprint = validate_model_fingerprint(&model_dir)?;
    println!("model fingerprint: {fingerprint}");
    let dict = convert_dict(&model_dir)?;
    fs::write(out_dir.join("dict.bin"), encode_asset(&dict))?;
    let bmes = convert_bmes(&model_dir)?;
    fs::write(out_dir.join("hmm_bmes.bin"), encode_asset(&bmes))?;
    let pos = convert_pos(&model_dir)?;
    fs::write(
        out_dir.join("hmm_pos.bin"),
        encode_quantized_pos_asset(&pos),
    )?;
    let taxonomy_text = fs::read_to_string(affect_dir.join("emotion-taxonomy.json"))
        .context("讀取 emotion-taxonomy.json")?;
    let lexicon_text = fs::read_to_string(affect_dir.join("emotion-lexicon.json"))
        .context("讀取 emotion-lexicon.json")?;
    let taxonomy = parse_taxonomy(&taxonomy_text).map_err(anyhow::Error::msg)?;
    let lexicon = parse_affect_lexicon(&lexicon_text).map_err(anyhow::Error::msg)?;
    let affect = build_affect_model(taxonomy, lexicon).map_err(anyhow::Error::msg)?;
    fs::write(out_dir.join("affect.bin"), encode_asset(&affect))?;
    println!(
        "[affect] taxonomy {}：{} 標籤、{} 詞、未知標籤 0",
        affect.taxonomy.version,
        affect.taxonomy.labels.len(),
        affect.entries.len()
    );
    let label_families: BTreeMap<_, _> = affect
        .taxonomy
        .labels
        .iter()
        .map(|label| (label.id.as_str(), label.family.as_str()))
        .collect();
    let mut family_counts: BTreeMap<&str, usize> = BTreeMap::new();
    for entry in &affect.entries {
        let mut seen = BTreeSet::new();
        for emotion in &entry.affect.emotions {
            if let Some(family) = label_families.get(emotion.as_str()) {
                seen.insert(*family);
            }
        }
        for family in seen {
            *family_counts.entry(family).or_default() += 1;
        }
    }
    println!("[affect] 各家族詞數: {family_counts:?}");
    print_report(&dict, &bmes, &pos);
    Ok(())
}
/// 讀取 JSON 檔（容忍 UTF-8 BOM，舊版 C# 輸出常帶）。
fn read_json(path: &Path) -> Result<Value> {
    let bytes = fs::read(path).with_context(|| format!("讀取 {}", path.display()))?;
    let slice = bytes
        .strip_prefix(b"\xef\xbb\xbf".as_slice())
        .unwrap_or(&bytes);
    serde_json::from_slice(slice).with_context(|| format!("解析 JSON {}", path.display()))
}

/// 將 JSON 的 f64 log 機率壓成 f32；-3.14E+100 等哨兵一律映為 MIN_LOG。
fn clamp_log(v: f64) -> f32 {
    if !v.is_finite() || v < -1.0e29 {
        MIN_LOG
    } else {
        v as f32
    }
}

/// 取 JSON 物件的字串 key 的首字元（模型 key 均為單一字元；多字元 key 屬異常，回 None 由呼叫端統計警告）。
fn single_char(key: &str) -> Option<char> {
    let mut it = key.chars();
    let c = it.next()?;
    it.next().is_none().then_some(c)
}

fn state_idx(s: &str) -> Result<usize> {
    STATES
        .iter()
        .position(|&x| x == s)
        .with_context(|| format!("未知 BMES 狀態 {s:?}"))
}

// ---------------------------------------------------------------------------
// 詞典轉換
// ---------------------------------------------------------------------------

/// 詞條中繼形式：合併 Dict.json 與 TaiwanDict.json 時使用。
struct RawEntry {
    tag: String,
    freq: f64,
}

/// 舊語料把「量詞＋名詞」或「數詞＋量詞＋名詞」誤收成整詞的已知家族。
/// 這些詞條會跨越 canonical 最小語意邊界，且其錯誤詞頻足以壓過正確 DAG 路徑。
const MEASURE_CROSSING_BASES: &[&str] = &["本書", "封信", "層樓"];

fn is_chinese_numeral(c: char) -> bool {
    matches!(
        c,
        '〇' | '零'
            | '一'
            | '二'
            | '三'
            | '四'
            | '五'
            | '六'
            | '七'
            | '八'
            | '九'
            | '十'
            | '百'
            | '千'
            | '萬'
            | '億'
            | '兆'
            | '兩'
    )
}

fn is_measure_crossing_noise(word: &str) -> bool {
    MEASURE_CROSSING_BASES.iter().any(|base| {
        word == *base
            || word
                .strip_suffix(base)
                .is_some_and(|prefix| !prefix.is_empty() && prefix.chars().all(is_chinese_numeral))
    })
}

fn convert_dict(resources: &Path) -> Result<DictModel> {
    // 異體字表：僅接受 UTF-8 等長映射，執行期以等長替換維持 byte offset 不變。
    let variant_json = read_json(&resources.join("VariantWords.json"))?;
    let mut variant_map: Vec<(char, char)> = Vec::new();
    for (k, v) in variant_json.as_object().context("VariantWords 非物件")? {
        let from = single_char(k).context("VariantWords key 非單字元")?;
        let to = single_char(v.as_str().context("VariantWords value 非字串")?)
            .context("VariantWords value 非單字元")?;
        if from.len_utf8() != to.len_utf8() {
            bail!("異體字 {from} → {to} UTF-8 長度不等，會破壞 byte offset 語意");
        }
        variant_map.push((from, to));
    }

    // 正規化：ASCII 小寫（等長）+ 異體字替換（已驗證等長）。
    // 與執行期文字正規化必須完全一致，否則詞典查不到。
    let normalize = |w: &str| -> String {
        w.chars()
            .map(|c| {
                let c = c.to_ascii_lowercase();
                variant_map
                    .iter()
                    .find(|(from, _)| *from == c)
                    .map(|(_, to)| *to)
                    .unwrap_or(c)
            })
            .collect()
    };

    // 只用主詞典。刻意排除的來源（皆為未審核的自動抽詞噪音，會產生
    // 假 DAG 邊搶走正詞，如「與國」搶「國民黨」、「一起去」搶「一起」）：
    //   - TaiwanDict.json：全部 unknown/freq 0 的 n-gram
    //   - 主詞典中 tag = unknownnew 的條目（舊版新詞識別的中間產物）
    let mut merged: BTreeMap<String, RawEntry> = BTreeMap::new();
    let mut skipped = 0usize;
    let mut noise = 0usize;
    let mut structural_noise = 0usize;
    let json = read_json(&resources.join("Dict.json"))?;
    for (word, arr) in json.as_object().context("詞典非物件")? {
        let word = normalize(word);
        if word.chars().count() < 2 || word.chars().count() > 255 {
            skipped += 1;
            continue;
        }
        let (tag, freq) = match (
            arr.get(0).and_then(Value::as_str),
            arr.get(1).and_then(Value::as_f64),
        ) {
            (Some(t), Some(f)) => (t.to_string(), f),
            _ => {
                skipped += 1;
                continue;
            }
        };
        if !freq.is_finite() || freq <= 0.0 {
            bail!("詞典詞 {word:?} 缺少有限正頻率；請先由私有原始計數補值");
        }
        if freq < MIN_DICTIONARY_FREQUENCY {
            bail!(
                "詞典詞 {word:?} 頻率 {freq} 低於最低門檻 {MIN_DICTIONARY_FREQUENCY}；請先重建 canonical Dict.json"
            );
        }
        if tag == "unknownnew" {
            noise += 1;
            continue;
        }
        if is_measure_crossing_noise(&word) {
            structural_noise += 1;
            continue;
        }
        match merged.get_mut(&word) {
            None => {
                merged.insert(word, RawEntry { tag, freq });
            }
            // 正規化後同形（如 臺灣/台灣）：取高頻，不用 unknown 覆蓋已知詞性。
            Some(old) => {
                if freq > old.freq {
                    if tag != "unknown" {
                        old.tag = tag;
                    }
                    old.freq = freq;
                } else if old.tag == "unknown" && tag != "unknown" {
                    old.tag = tag;
                }
            }
        }
    }
    println!(
        "[dict] 略過無法解析 {skipped} 筆、unknownnew 噪音 {noise} 筆、量詞跨界噪音 {structural_noise} 筆"
    );

    let total: f64 = merged.values().map(|entry| entry.freq).sum();
    let total_log = total.ln() as f32;

    // 詞性標籤表：資料驅動，掃描所有出現過的標籤字串。
    let tag_set: BTreeSet<&str> = merged.values().map(|e| e.tag.as_str()).collect();
    if tag_set.len() > 255 {
        bail!("詞性標籤種類超過 255，u8 id 不足");
    }
    let tag_names: Vec<String> = tag_set.iter().map(|s| s.to_string()).collect();
    let tag_id = |t: &str| tag_names.iter().position(|x| x == t).unwrap() as u8;

    // SoA 詞條表 + AC 自動機（BTreeMap 迭代順序即詞條 id 順序）。
    let mut word_tags = Vec::with_capacity(merged.len());
    let mut word_log_probs = Vec::with_capacity(merged.len());
    let mut word_char_lens = Vec::with_capacity(merged.len());
    let mut patterns: Vec<(&str, u32)> = Vec::with_capacity(merged.len());
    for (i, (word, entry)) in merged.iter().enumerate() {
        word_tags.push(tag_id(&entry.tag));
        word_log_probs.push((entry.freq.ln() - total.ln()) as f32);
        word_char_lens.push(word.chars().count() as u8);
        patterns.push((word.as_str(), i as u32));
    }
    let automaton: CharwiseDoubleArrayAhoCorasick<u32> =
        CharwiseDoubleArrayAhoCorasick::with_values(patterns)
            .map_err(|e| anyhow::anyhow!("建立 AC 自動機失敗: {e}"))?;

    Ok(DictModel {
        automaton_bytes: automaton.serialize(),
        tag_names,
        word_tags,
        word_log_probs,
        word_char_lens,
        total_log,
        variant_map,
    })
}

// ---------------------------------------------------------------------------
// BMES HMM 轉換
// ---------------------------------------------------------------------------

fn validate_log_distribution(label: &str, values: impl IntoIterator<Item = f32>) -> Result<()> {
    let mut sum = 0.0f64;
    for value in values {
        if !value.is_finite() {
            bail!("{label} 含 NaN/Infinity");
        }
        if value > MIN_LOG {
            sum += (value as f64).exp();
        }
    }
    if (sum - 1.0).abs() > 2.0e-4 {
        bail!("{label} 未正規化：exp(logp) 總和為 {sum:.9}");
    }
    Ok(())
}

fn convert_bmes(resources: &Path) -> Result<BmesModel> {
    let start_json = read_json(&resources.join("startProbs.json"))?;
    let trans1_json = read_json(&resources.join("transProbs.json"))?;
    let trans2_json = read_json(&resources.join("transProbs2.json"))?;
    let emit1_json = read_json(&resources.join("emmitProbs.json"))?;
    let emit2_json = read_json(&resources.join("emmitProbs2.json"))?;

    let mut start = [MIN_LOG; 4];
    for (name, value) in start_json.as_object().context("startProbs 非物件")? {
        start[state_idx(name)?] = clamp_log(value.as_f64().context("startProbs 值非數字")?);
    }
    validate_log_distribution("BMES start", start)?;

    let mut trans1 = [[MIN_LOG; 4]; 4];
    for (previous, row) in trans1_json.as_object().context("transProbs 非物件")? {
        let pi = state_idx(previous)?;
        for (current, value) in row.as_object().context("transProbs 內層非物件")? {
            trans1[pi][state_idx(current)?] = clamp_log(value.as_f64().context("值非數字")?);
        }
        validate_log_distribution(&format!("BMES trans1[{previous}]"), trans1[pi])?;
    }

    let mut trans2 = [[[MIN_LOG; 4]; 4]; 4];
    for (previous2, middle) in trans2_json.as_object().context("transProbs2 非物件")? {
        let p2 = state_idx(previous2)?;
        for (previous1, row) in middle.as_object().context("transProbs2 中層非物件")? {
            let p1 = state_idx(previous1)?;
            for (current, value) in row.as_object().context("transProbs2 內層非物件")? {
                trans2[p2][p1][state_idx(current)?] =
                    clamp_log(value.as_f64().context("值非數字")?);
            }
            validate_log_distribution(
                &format!("BMES trans2[{previous2}][{previous1}]"),
                trans2[p2][p1],
            )?;
        }
    }

    let mut char_set = BTreeSet::new();
    for row in emit1_json
        .as_object()
        .context("emmitProbs 非物件")?
        .values()
    {
        for key in row.as_object().context("emmitProbs 內層非物件")?.keys() {
            if key != "<UNK>" {
                let character =
                    single_char(key).with_context(|| format!("emmitProbs 非單字元 key {key:?}"))?;
                char_set.insert(character);
            }
        }
    }
    for middle in emit2_json
        .as_object()
        .context("emmitProbs2 非物件")?
        .values()
    {
        for row in middle
            .as_object()
            .context("emmitProbs2 中層非物件")?
            .values()
        {
            for key in row.as_object().context("emmitProbs2 內層非物件")?.keys() {
                if key != "<UNK>" {
                    let character = single_char(key)
                        .with_context(|| format!("emmitProbs2 非單字元 key {key:?}"))?;
                    char_set.insert(character);
                }
            }
        }
    }
    let chars = CharTable {
        chars: char_set.into_iter().collect(),
    };
    let index = |character: char| chars.index_of(character).unwrap();

    let mut emit1_unknown = [MIN_LOG; 4];
    for (state, row) in emit1_json.as_object().unwrap() {
        let si = state_idx(state)?;
        let row = row.as_object().context("emmitProbs 內層非物件")?;
        emit1_unknown[si] = clamp_log(
            row.get("<UNK>")
                .context("emmitProbs 缺少 <UNK>")?
                .as_f64()
                .context("<UNK> 非數字")?,
        );
    }
    let mut emit1 = vec![emit1_unknown; chars.chars.len()];
    for (state, row) in emit1_json.as_object().unwrap() {
        let si = state_idx(state)?;
        for (key, value) in row.as_object().unwrap() {
            if key == "<UNK>" {
                continue;
            }
            emit1[index(single_char(key).unwrap())][si] =
                clamp_log(value.as_f64().context("值非數字")?);
        }
    }
    for state in 0..4 {
        validate_log_distribution(
            &format!("BMES emit1[{}]", STATES[state]),
            emit1
                .iter()
                .map(|row| row[state])
                .chain([emit1_unknown[state]]),
        )?;
    }

    let mut emit2_unknown = [[MIN_LOG; 4]; 4];
    for (previous, middle) in emit2_json.as_object().unwrap() {
        let pi = state_idx(previous)?;
        for (current, row) in middle.as_object().context("emmitProbs2 中層非物件")? {
            let ci = state_idx(current)?;
            let row = row.as_object().context("emmitProbs2 內層非物件")?;
            emit2_unknown[pi][ci] = clamp_log(
                row.get("<UNK>")
                    .context("emmitProbs2 缺少 <UNK>")?
                    .as_f64()
                    .context("<UNK> 非數字")?,
            );
        }
    }
    let mut emit2 = vec![emit2_unknown; chars.chars.len()];
    for (previous, middle) in emit2_json.as_object().unwrap() {
        let pi = state_idx(previous)?;
        for (current, row) in middle.as_object().unwrap() {
            let ci = state_idx(current)?;
            for (key, value) in row.as_object().context("emmitProbs2 內層非物件")? {
                if key == "<UNK>" {
                    continue;
                }
                emit2[index(single_char(key).unwrap())][pi][ci] =
                    clamp_log(value.as_f64().context("值非數字")?);
            }
        }
    }
    for previous in 0..4 {
        for current in 0..4 {
            if emit2_unknown[previous][current] <= MIN_LOG {
                continue;
            }
            validate_log_distribution(
                &format!("BMES emit2[{}][{}]", STATES[previous], STATES[current]),
                emit2
                    .iter()
                    .map(|row| row[previous][current])
                    .chain([emit2_unknown[previous][current]]),
            )?;
        }
    }

    Ok(BmesModel {
        chars,
        start,
        trans1,
        trans2,
        emit1,
        emit2,
        emit1_unknown,
        emit2_unknown,
    })
}
// ---------------------------------------------------------------------------
// POS HMM 轉換
// ---------------------------------------------------------------------------

fn convert_pos(resources: &Path) -> Result<PosModel> {
    let start_json = read_json(&resources.join("tagStartProbs.json"))?;
    let trans1_json = read_json(&resources.join("tagTransProbs.json"))?;
    let trans2_json = read_json(&resources.join("tagTransProbs2.json"))?;
    let emit_json = read_json(&resources.join("tagEmitProbs.json"))?;
    let lexicon_json = read_json(&resources.join("PosLexicon.json"))?;

    let mut state_set = BTreeSet::new();
    for name in start_json
        .as_object()
        .context("tagStartProbs 非物件")?
        .keys()
    {
        state_set.insert(name.clone());
    }
    for (previous, row) in trans1_json.as_object().context("tagTransProbs 非物件")? {
        state_set.insert(previous.clone());
        state_set.extend(
            row.as_object()
                .context("tagTransProbs 內層非物件")?
                .keys()
                .cloned(),
        );
    }
    for (previous2, middle) in trans2_json.as_object().context("tagTransProbs2 非物件")? {
        state_set.insert(previous2.clone());
        for (previous1, row) in middle.as_object().context("tagTransProbs2 中層非物件")? {
            state_set.insert(previous1.clone());
            state_set.extend(
                row.as_object()
                    .context("tagTransProbs2 內層非物件")?
                    .keys()
                    .cloned(),
            );
        }
    }
    state_set.extend(
        emit_json
            .as_object()
            .context("tagEmitProbs 非物件")?
            .keys()
            .cloned(),
    );

    let state_names: Vec<String> = state_set.into_iter().collect();
    let n = state_names.len();
    if n == 0 || n > u16::MAX as usize {
        bail!("POS 狀態數不合法：{n}");
    }
    let sid = |name: &str| -> Result<usize> {
        state_names
            .binary_search_by(|value| value.as_str().cmp(name))
            .map_err(|_| anyhow::anyhow!("內部錯誤：狀態 {name} 不在全集"))
    };

    let mut tag_set = BTreeSet::new();
    let mut parsed = Vec::with_capacity(n);
    for name in &state_names {
        let (bmes, tag) = name
            .split_once('-')
            .with_context(|| format!("狀態名 {name:?} 非 BMES-tag 格式"))?;
        parsed.push((state_idx(bmes)?, tag.to_string()));
        tag_set.insert(tag.to_string());
    }
    if tag_set.len() > u8::MAX as usize {
        bail!("POS 詞性種類超過 255");
    }
    let tag_names: Vec<String> = tag_set.into_iter().collect();
    let state_bmes = parsed.iter().map(|(state, _)| *state as u8).collect();
    let state_tags = parsed
        .iter()
        .map(|(_, tag)| tag_names.binary_search(tag).unwrap() as u8)
        .collect();

    let mut start = vec![MIN_LOG; n];
    for (name, value) in start_json.as_object().unwrap() {
        start[sid(name)?] = clamp_log(value.as_f64().context("值非數字")?);
    }
    validate_log_distribution("POS start", start.iter().copied())?;

    let mut trans1 = vec![MIN_LOG; n * n];
    for (previous, row) in trans1_json.as_object().unwrap() {
        let pi = sid(previous)?;
        for (current, value) in row.as_object().context("tagTransProbs 內層非物件")? {
            trans1[pi * n + sid(current)?] = clamp_log(value.as_f64().context("值非數字")?);
        }
        validate_log_distribution(
            &format!("POS trans1[{previous}]"),
            trans1[pi * n..(pi + 1) * n].iter().copied(),
        )?;
    }

    let cube_len = n
        .checked_mul(n)
        .and_then(|x| x.checked_mul(n))
        .context("POS 二階矩陣大小溢位")?;
    let mut trans2 = vec![MIN_LOG; cube_len];
    for (previous2, middle) in trans2_json.as_object().unwrap() {
        let p2 = sid(previous2)?;
        for (previous1, row) in middle.as_object().context("tagTransProbs2 中層非物件")? {
            let p1 = sid(previous1)?;
            let base = (p2 * n + p1) * n;
            for (current, value) in row.as_object().context("tagTransProbs2 內層非物件")? {
                trans2[base + sid(current)?] = clamp_log(value.as_f64().context("值非數字")?);
            }
            validate_log_distribution(
                &format!("POS trans2[{previous2}][{previous1}]"),
                trans2[base..base + n].iter().copied(),
            )?;
        }
    }

    let mut char_set = BTreeSet::new();
    for row in emit_json.as_object().unwrap().values() {
        for key in row.as_object().context("tagEmitProbs 內層非物件")?.keys() {
            if key != "<UNK>" {
                char_set.insert(
                    single_char(key)
                        .with_context(|| format!("tagEmitProbs 非單字元 key {key:?}"))?,
                );
            }
        }
    }
    let chars = CharTable {
        chars: char_set.into_iter().collect(),
    };
    let mut emit_unknown = vec![MIN_LOG; n];
    let mut per_char: BTreeMap<char, BTreeMap<u16, f32>> = BTreeMap::new();
    for (state, row) in emit_json.as_object().unwrap() {
        let state_id = sid(state)?;
        let row = row.as_object().context("tagEmitProbs 內層非物件")?;
        emit_unknown[state_id] = clamp_log(
            row.get("<UNK>")
                .context("tagEmitProbs 缺少 <UNK>")?
                .as_f64()
                .context("<UNK> 非數字")?,
        );
        for (key, value) in row {
            if key == "<UNK>" {
                continue;
            }
            per_char
                .entry(single_char(key).unwrap())
                .or_default()
                .insert(
                    state_id as u16,
                    clamp_log(value.as_f64().context("值非數字")?),
                );
        }
    }
    for state in 0..n {
        if emit_unknown[state] <= MIN_LOG {
            continue;
        }
        validate_log_distribution(
            &format!("POS emit[{}]", state_names[state]),
            chars
                .chars
                .iter()
                .map(|character| {
                    per_char
                        .get(character)
                        .and_then(|row| row.get(&(state as u16)))
                        .copied()
                        .unwrap_or(emit_unknown[state])
                })
                .chain([emit_unknown[state]]),
        )?;
    }
    let mut emit_offsets = Vec::with_capacity(chars.chars.len() + 1);
    let mut emit_states = Vec::new();
    let mut emit_logps = Vec::new();
    emit_offsets.push(0);
    for character in &chars.chars {
        if let Some(row) = per_char.get(character) {
            for (&state, &logp) in row {
                emit_states.push(state);
                emit_logps.push(logp);
            }
        }
        emit_offsets.push(emit_states.len() as u32);
    }

    let mut words = Vec::new();
    let mut lexicon_offsets = Vec::new();
    let mut lexicon_tags = Vec::new();
    let mut lexicon_logps = Vec::new();
    lexicon_offsets.push(0);
    for (word, raw_row) in lexicon_json.as_object().context("PosLexicon 非物件")? {
        if word.is_empty() {
            continue;
        }
        let row = raw_row.as_object().context("PosLexicon 內層非物件")?;
        let total: f64 = row
            .values()
            .map(|value| value.as_f64().unwrap_or(f64::NAN))
            .sum();
        if !total.is_finite() || total <= 0.0 {
            bail!("PosLexicon 詞 {word:?} 的總頻率不合法");
        }
        let mut entries = Vec::new();
        for (tag, value) in row {
            let frequency = value.as_f64().context("PosLexicon 頻率非數字")?;
            if !frequency.is_finite() || frequency <= 0.0 {
                bail!("PosLexicon 詞 {word:?} 詞性 {tag:?} 頻率不合法");
            }
            let tag_id = tag_names
                .binary_search(tag)
                .map_err(|_| anyhow::anyhow!("PosLexicon 使用未知詞性 {tag:?}"))?
                as u8;
            entries.push((tag_id, (frequency / total).ln() as f32));
        }
        entries.sort_by_key(|entry| entry.0);
        validate_log_distribution(
            &format!("PosLexicon[{word}]"),
            entries.iter().map(|entry| entry.1),
        )?;
        words.push(word.clone());
        for (tag, logp) in entries {
            lexicon_tags.push(tag);
            lexicon_logps.push(logp);
        }
        lexicon_offsets.push(lexicon_tags.len() as u32);
    }
    let patterns = words
        .iter()
        .enumerate()
        .map(|(id, word)| (word.as_str(), id as u32));
    let lexicon = CharwiseDoubleArrayAhoCorasick::<u32>::with_values(patterns)
        .map_err(|error| anyhow::anyhow!("POS 詞彙自動機建構失敗: {error}"))?;

    Ok(PosModel {
        state_names,
        state_bmes,
        state_tags,
        tag_names,
        start,
        trans1,
        trans2,
        chars,
        emit_offsets,
        emit_states,
        emit_logps,
        emit_unknown,
        lexicon_automaton_bytes: lexicon.serialize(),
        lexicon_offsets,
        lexicon_tags,
        lexicon_logps,
    })
}
// ---------------------------------------------------------------------------
// 轉換報告與 spot-check
// ---------------------------------------------------------------------------

fn print_report(dict: &DictModel, bmes: &BmesModel, pos: &PosModel) {
    println!("\n=== 轉換報告 ===");
    println!(
        "[dict] 詞條 {} 筆, 詞性 {} 種, 自動機 {:.1} MB, total_log = {:.4}",
        dict.word_tags.len(),
        dict.tag_names.len(),
        dict.automaton_bytes.len() as f64 / 1e6,
        dict.total_log,
    );
    println!("[dict] 詞性表: {:?}", dict.tag_names);
    println!("[dict] 異體字映射 {} 對", dict.variant_map.len());

    println!(
        "[bmes] 字元 {} 個, start = {:?}",
        bmes.chars.chars.len(),
        bmes.start,
    );
    // spot-check：對照 transProbs.json 的 B→E 應為 -0.13620961
    println!(
        "[bmes] trans1[B][E] = {:.6}, trans2[B][M][E] = {:.6}",
        bmes.trans1[0][2], bmes.trans2[0][1][2],
    );

    println!(
        "[pos] 狀態 {} 個, 詞性 {} 種, 字元 {} 個, 發射項 {} 筆, 詞彙 {} 筆",
        pos.state_names.len(),
        pos.tag_names.len(),
        pos.chars.chars.len(),
        pos.emit_states.len(),
        pos.lexicon_offsets.len().saturating_sub(1),
    );
    if let Some((states, logps)) = pos.emit_row('耀') {
        println!(
            "[pos] '耀' 允許狀態 {} 個, 首項 = {} @ {:.4}",
            states.len(),
            pos.state_names[states[0] as usize],
            logps[0],
        );
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measure_crossing_lint_rejects_known_noise_families() {
        for word in ["本書", "封信", "層樓", "一本書", "兩封信", "三層樓"] {
            assert!(is_measure_crossing_noise(word), "應排除 {word}");
        }
        for word in ["書本", "封面", "樓層", "一本正經", "星期", "個人"] {
            assert!(!is_measure_crossing_noise(word), "不應排除 {word}");
        }
    }
}
