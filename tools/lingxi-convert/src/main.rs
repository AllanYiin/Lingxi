//! lingxi-convert：將舊版 LingXi 的 JSON 模型資產轉為 postcard 二進位。
//!
//! 用法（參數皆可省略，預設對應本 repo 的相對位置）：
//!   lingxi-convert [resources_dir] [modeling_dir] [out_dir]
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
    encode_asset, BmesModel, CharTable, DictModel, PosModel, MIN_LOG,
};

/// POS 發射機率缺項地板值：狀態合法（char_state_tab 允許）但無發射統計時，
/// 給「極不可能但仍可走」的 log 機率，避免路徑被 MIN_LOG 直接殺死。
const EMIT_FLOOR: f32 = -50.0;

/// BMES 狀態固定順序，與 lingxi_core::model 的 STATE_* 對齊。
const STATES: [&str; 4] = ["B", "M", "E", "S"];

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let resources = dir_arg(&args, 0, r"..\LingXi\Resources");
    let modeling = dir_arg(&args, 1, r"..\ModelingData");
    let out_dir = dir_arg(&args, 2, "assets");
    fs::create_dir_all(&out_dir)?;

    println!("resources: {}", resources.display());
    println!("modeling : {}", modeling.display());
    println!("out      : {}\n", out_dir.display());

    let dict = convert_dict(&resources, &modeling)?;
    fs::write(out_dir.join("dict.bin"), encode_asset(&dict))?;

    let bmes = convert_bmes(&resources)?;
    fs::write(out_dir.join("hmm_bmes.bin"), encode_asset(&bmes))?;

    let pos = convert_pos(&resources)?;
    fs::write(out_dir.join("hmm_pos.bin"), encode_asset(&pos))?;

    print_report(&dict, &bmes, &pos);
    Ok(())
}

/// 取第 i 個位置參數，缺省時用預設路徑。
fn dir_arg(args: &[String], i: usize, default: &str) -> PathBuf {
    args.get(i).map(PathBuf::from).unwrap_or_else(|| PathBuf::from(default))
}

/// 讀取 JSON 檔（容忍 UTF-8 BOM，舊版 C# 輸出常帶）。
fn read_json(path: &Path) -> Result<Value> {
    let bytes = fs::read(path).with_context(|| format!("讀取 {}", path.display()))?;
    let slice = bytes.strip_prefix(b"\xef\xbb\xbf".as_slice()).unwrap_or(&bytes);
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

fn convert_dict(resources: &Path, modeling: &Path) -> Result<DictModel> {
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
    // 主詞典中 freq=0 但有真實詞性者為正規詞彙表，保留（0.5 平滑）。
    let _ = modeling; // TaiwanDict 所在目錄，現已不使用；保留參數以維持 CLI 介面
    let mut merged: BTreeMap<String, RawEntry> = BTreeMap::new();
    let mut skipped = 0usize;
    let mut noise = 0usize;
    let json = read_json(&resources.join("Dict.json"))?;
    for (word, arr) in json.as_object().context("詞典非物件")? {
        let word = normalize(word);
        if word.is_empty() || word.chars().count() > 255 {
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
        if tag == "unknownnew" {
            noise += 1;
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
    println!("[dict] 略過無法解析 {skipped} 筆、unknownnew 噪音 {noise} 筆");

    // 頻率 0 的詞（多來自 TaiwanDict）以 0.5 平滑，避免 log(0)。
    let effective = |freq: f64| if freq > 0.0 { freq } else { 0.5 };
    let total: f64 = merged.values().map(|e| effective(e.freq)).sum();
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
        word_log_probs.push((effective(entry.freq).ln() - total.ln()) as f32);
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

fn convert_bmes(resources: &Path) -> Result<BmesModel> {
    let start_json = read_json(&resources.join("startProbs.json"))?;
    let trans1_json = read_json(&resources.join("transProbs.json"))?;
    let trans2_json = read_json(&resources.join("transProbs2.json"))?;
    let emit1_json = read_json(&resources.join("emmitProbs.json"))?;
    let emit2_json = read_json(&resources.join("emmitProbs2.json"))?;
    let r_emit_json = read_json(&resources.join("r_emmitProbs.json"))?;

    let mut start = [MIN_LOG; 4];
    for (k, v) in start_json.as_object().context("startProbs 非物件")? {
        start[state_idx(k)?] = clamp_log(v.as_f64().context("startProbs 值非數字")?);
    }

    let mut trans1 = [[MIN_LOG; 4]; 4];
    for (prev, row) in trans1_json.as_object().context("transProbs 非物件")? {
        let pi = state_idx(prev)?;
        for (cur, v) in row.as_object().context("transProbs 內層非物件")? {
            trans1[pi][state_idx(cur)?] = clamp_log(v.as_f64().context("值非數字")?);
        }
    }

    let mut trans2 = [[[MIN_LOG; 4]; 4]; 4];
    for (p2, mid) in trans2_json.as_object().context("transProbs2 非物件")? {
        let i2 = state_idx(p2)?;
        for (p1, row) in mid.as_object().context("transProbs2 中層非物件")? {
            let i1 = state_idx(p1)?;
            for (cur, v) in row.as_object().context("transProbs2 內層非物件")? {
                trans2[i2][i1][state_idx(cur)?] = clamp_log(v.as_f64().context("值非數字")?);
            }
        }
    }

    // 字元表 = emit1 / emit2 / r_emit 出現過的字元聯集。
    let mut char_set: BTreeSet<char> = BTreeSet::new();
    for (_, row) in emit1_json.as_object().context("emmitProbs 非物件")? {
        for (k, _) in row.as_object().context("emmitProbs 內層非物件")? {
            char_set.extend(single_char(k));
        }
    }
    for (_, mid) in emit2_json.as_object().context("emmitProbs2 非物件")? {
        for (_, row) in mid.as_object().context("emmitProbs2 中層非物件")? {
            for (k, _) in row.as_object().context("emmitProbs2 內層非物件")? {
                char_set.extend(single_char(k));
            }
        }
    }
    for (k, _) in r_emit_json.as_object().context("r_emmitProbs 非物件")? {
        char_set.extend(single_char(k));
    }
    let chars = CharTable { chars: char_set.into_iter().collect() };
    let idx = |c: char| chars.index_of(c).unwrap();

    let mut emit1 = vec![[MIN_LOG; 4]; chars.chars.len()];
    for (state, row) in emit1_json.as_object().unwrap() {
        let si = state_idx(state)?;
        for (k, v) in row.as_object().unwrap() {
            if let Some(c) = single_char(k) {
                emit1[idx(c)][si] = clamp_log(v.as_f64().context("值非數字")?);
            }
        }
    }

    let mut emit2 = vec![[[MIN_LOG; 4]; 4]; chars.chars.len()];
    for (prev, mid) in emit2_json.as_object().unwrap() {
        let pi = state_idx(prev)?;
        for (cur, row) in mid.as_object().unwrap() {
            let ci = state_idx(cur)?;
            for (k, v) in row.as_object().unwrap() {
                if let Some(c) = single_char(k) {
                    emit2[idx(c)][pi][ci] = clamp_log(v.as_f64().context("值非數字")?);
                }
            }
        }
    }

    // r_emit：原始機率 → log；無統計的字元填 start 值，
    // 使執行期首字公式 r_emit[c][s] + emit1[c][s] 無須 fallback 分支。
    let mut r_emit = vec![start; chars.chars.len()];
    for (k, row) in r_emit_json.as_object().unwrap() {
        let Some(c) = single_char(k) else { continue };
        let mut vals = [MIN_LOG; 4];
        for (state, v) in row.as_object().context("r_emmitProbs 內層非物件")? {
            let p = v.as_f64().context("值非數字")?;
            vals[state_idx(state)?] = if p > 0.0 { (p.ln()) as f32 } else { MIN_LOG };
        }
        r_emit[idx(c)] = vals;
    }

    Ok(BmesModel { chars, start, trans1, trans2, emit1, emit2, r_emit })
}

// ---------------------------------------------------------------------------
// POS HMM 轉換
// ---------------------------------------------------------------------------

fn convert_pos(resources: &Path) -> Result<PosModel> {
    let start_json = read_json(&resources.join("tagStartProbs.json"))?;
    let trans_json = read_json(&resources.join("tagTransProbs.json"))?;
    let emit_json = read_json(&resources.join("tagEmitProbs.json"))?;
    let cst_json = read_json(&resources.join("char_state_tab.json"))?;

    // 狀態全集 = 四個來源出現過的 joint 狀態名聯集（如 "B-a"）。
    let mut state_set: BTreeSet<String> = BTreeSet::new();
    for (k, _) in start_json.as_object().context("tagStartProbs 非物件")? {
        state_set.insert(k.clone());
    }
    for (k, row) in trans_json.as_object().context("tagTransProbs 非物件")? {
        state_set.insert(k.clone());
        for (k2, _) in row.as_object().context("tagTransProbs 內層非物件")? {
            state_set.insert(k2.clone());
        }
    }
    for (k, _) in emit_json.as_object().context("tagEmitProbs 非物件")? {
        state_set.insert(k.clone());
    }
    for (_, states) in cst_json.as_object().context("char_state_tab 非物件")? {
        for s in states.as_array().context("char_state_tab 值非陣列")? {
            state_set.insert(s.as_str().context("狀態名非字串")?.to_string());
        }
    }

    let state_names: Vec<String> = state_set.into_iter().collect();
    let n = state_names.len();
    if n > u16::MAX as usize {
        bail!("POS 狀態數超過 u16 範圍");
    }
    let sid = |name: &str| -> Result<usize> {
        state_names
            .binary_search_by(|x| x.as_str().cmp(name))
            .map_err(|_| anyhow::anyhow!("內部錯誤：狀態 {name} 不在全集"))
    };

    // 解析 "B-a" → (bmes, tag)；tag 表資料驅動。
    let mut tag_set: BTreeSet<String> = BTreeSet::new();
    let mut parsed: Vec<(usize, String)> = Vec::with_capacity(n);
    for name in &state_names {
        let (bmes, tag) = name
            .split_once('-')
            .with_context(|| format!("狀態名 {name:?} 非 BMES-tag 格式"))?;
        parsed.push((state_idx(bmes)?, tag.to_string()));
        tag_set.insert(tag.to_string());
    }
    if tag_set.len() > 255 {
        bail!("POS 詞性種類超過 255");
    }
    let tag_names: Vec<String> = tag_set.into_iter().collect();
    let state_bmes: Vec<u8> = parsed.iter().map(|(b, _)| *b as u8).collect();
    let state_tags: Vec<u8> = parsed
        .iter()
        .map(|(_, t)| tag_names.iter().position(|x| x == t).unwrap() as u8)
        .collect();

    let mut start = vec![MIN_LOG; n];
    for (k, v) in start_json.as_object().unwrap() {
        start[sid(k)?] = clamp_log(v.as_f64().context("值非數字")?);
    }

    let mut trans = vec![MIN_LOG; n * n];
    for (prev, row) in trans_json.as_object().unwrap() {
        let pi = sid(prev)?;
        for (cur, v) in row.as_object().unwrap() {
            trans[pi * n + sid(cur)?] = clamp_log(v.as_f64().context("值非數字")?);
        }
    }

    // 發射 CSR：每字元的允許狀態集 = char_state_tab 允許 ∪ 有發射統計者；
    // 機率取 tagEmitProbs，缺項用 EMIT_FLOOR。
    let mut per_char: BTreeMap<char, BTreeMap<u16, f32>> = BTreeMap::new();
    for (c, states) in cst_json.as_object().unwrap() {
        let Some(c) = single_char(c) else { continue };
        let row = per_char.entry(c).or_default();
        for s in states.as_array().unwrap() {
            row.insert(sid(s.as_str().unwrap())? as u16, EMIT_FLOOR);
        }
    }
    for (state, row) in emit_json.as_object().unwrap() {
        let si = sid(state)? as u16;
        for (k, v) in row.as_object().context("tagEmitProbs 內層非物件")? {
            let Some(c) = single_char(k) else { continue };
            per_char
                .entry(c)
                .or_default()
                .insert(si, clamp_log(v.as_f64().context("值非數字")?));
        }
    }

    let chars = CharTable { chars: per_char.keys().copied().collect() };
    let mut emit_offsets: Vec<u32> = Vec::with_capacity(per_char.len() + 1);
    let mut emit_states: Vec<u16> = Vec::new();
    let mut emit_logps: Vec<f32> = Vec::new();
    emit_offsets.push(0);
    for row in per_char.values() {
        for (&s, &p) in row {
            emit_states.push(s);
            emit_logps.push(p);
        }
        emit_offsets.push(emit_states.len() as u32);
    }

    Ok(PosModel {
        state_names,
        state_bmes,
        state_tags,
        tag_names,
        start,
        trans,
        chars,
        emit_offsets,
        emit_states,
        emit_logps,
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
        "[bmes] trans1[B][E] = {:.6} (JSON 原值 -0.136210), trans2[B][M][E] = {:.6} (JSON 原值 -0.466145)",
        bmes.trans1[0][2],
        bmes.trans2[0][1][2],
    );
    if let Some(i) = bmes.chars.index_of('的') {
        println!(
            "[bmes] emit1['的'] = {:?}, r_emit['的'] = {:?}",
            bmes.emit1[i], bmes.r_emit[i]
        );
    }

    println!(
        "[pos] 狀態 {} 個, 詞性 {} 種, 字元 {} 個, 發射項 {} 筆",
        pos.state_names.len(),
        pos.tag_names.len(),
        pos.chars.chars.len(),
        pos.emit_states.len(),
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
