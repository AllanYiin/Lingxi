//! 模型資料結構與二進位資產（asset）編解碼。
//!
//! 所有模型由 tools/lingxi-convert 離線從舊版 JSON 轉換；執行期只做
//! postcard 反序列化，零 JSON 解析。機率一律為 f32 log 域。
//!
//! 資產檔格式：4 bytes magic "LXA2" + u16 LE version + u64 LE xxh3(payload) + payload。

use serde::{de::DeserializeOwned, Deserialize, Serialize};

/// log 機率下限哨兵（對應舊版 C# 的 -3.14E+100），語意為「不可能」。
/// 不用 f32::NEG_INFINITY 是為了讓加法不產生 NaN（-inf + inf 等病態情況）。
pub const MIN_LOG: f32 = -1.0e30;

/// 資產檔頭 magic。
pub const ASSET_MAGIC: [u8; 4] = *b"LXA2";
/// 資產格式版本，結構有不相容變更時遞增。
pub const ASSET_VERSION: u16 = 2;

/// BMES 狀態索引固定順序：B=0, M=1, E=2, S=3。
pub const STATE_B: usize = 0;
pub const STATE_M: usize = 1;
pub const STATE_E: usize = 2;
pub const STATE_S: usize = 3;

// ---------------------------------------------------------------------------
// 共用：字元索引表
// ---------------------------------------------------------------------------

/// 排序後的字元表；以二分搜尋將 char 映射為 dense 陣列索引。
/// 取代舊版兩層 HashMap<char, ...>，查詢後直接讀取連續記憶體列。
#[derive(Serialize, Deserialize)]
pub struct CharTable {
    /// 已排序、去重的字元集合。
    pub chars: Vec<char>,
}

impl CharTable {
    /// 回傳字元在 dense 陣列中的列索引；不在表中回傳 None。
    #[inline]
    pub fn index_of(&self, c: char) -> Option<usize> {
        self.chars.binary_search(&c).ok()
    }
}

// ---------------------------------------------------------------------------
// 詞典模型
// ---------------------------------------------------------------------------

/// 詞典模型：daachorse AC 自動機 + SoA 詞條表。
///
/// 自動機一次 `find_overlapping_iter` 掃出句中所有詞典命中，
/// match value 即詞條 id，用以索引下方平行陣列。
#[derive(Serialize, Deserialize)]
pub struct DictModel {
    /// daachorse CharwiseDoubleArrayAhoCorasick<u32> 的序列化 bytes。
    pub automaton_bytes: Vec<u8>,
    /// 詞性標籤名稱表（資料驅動，非硬編 enum）；詞條 tag 為此表索引。
    pub tag_names: Vec<String>,
    /// 每個詞條的詞性標籤 id。
    pub word_tags: Vec<u8>,
    /// 每個詞條的 ln(freq / total)。
    pub word_log_probs: Vec<f32>,
    /// 每個詞條的字元數（DP 時免重算）。
    pub word_char_lens: Vec<u8>,
    /// ln(total_freq)：僅計入正頻率的多字詞條。
    pub total_log: f32,
    /// 異體字正規化映射（如 体→體、臺→台）；僅含 UTF-8 等長對，維持 byte offset 不變。
    pub variant_map: Vec<(char, char)>,
}

// ---------------------------------------------------------------------------
// BMES 分詞 HMM（含二階）
// ---------------------------------------------------------------------------

/// 未登入詞切分用的 BMES HMM，一階與二階矩陣皆含。
/// 解碼採二階 Viterbi：複合狀態 (prev, cur) 共 16 態的標準 DP。
#[derive(Serialize, Deserialize)]
pub struct BmesModel {
    /// 發射機率共用的完整訓練字元表。
    pub chars: CharTable,
    /// 初始機率 log P(s0)。
    pub start: [f32; 4],
    /// 一階轉移 log P(cur | prev)，索引 [prev][cur]。
    pub trans1: [[f32; 4]; 4],
    /// 二階轉移 log P(cur | prev2, prev1)，索引 [prev2][prev1][cur]。
    pub trans2: [[[f32; 4]; 4]; 4],
    /// 一階發射 log P(char | s)，每字元一列 [s]。
    pub emit1: Vec<[f32; 4]>,
    /// 二階發射 log P(char | prev, cur)，每字元一列 [prev][cur]。
    pub emit2: Vec<[[f32; 4]; 4]>,
    /// 一階發射的 `<UNK>` 平滑機率，索引 [state]。
    pub emit1_unknown: [f32; 4],
    /// 二階發射的 `<UNK>` 平滑機率，索引 [prev][cur]。
    pub emit2_unknown: [[f32; 4]; 4],
}

// ---------------------------------------------------------------------------
// POS 標註 HMM（joint state：BMES × 詞性）
// ---------------------------------------------------------------------------

/// OOV 詞性標註用的 joint-state HMM（狀態如 "B-a"、"E-nr"）。
/// 詞典詞的詞性直接查 DictModel，不經過此模型。
#[derive(Serialize, Deserialize)]
pub struct PosModel {
    /// 狀態名稱（如 "B-a"），索引即 state id。
    pub state_names: Vec<String>,
    /// 每個狀態的 BMES 部分（0..=3，對應 STATE_*）。
    pub state_bmes: Vec<u8>,
    /// 每個狀態的詞性部分，為 tag_names 索引。
    pub state_tags: Vec<u8>,
    /// POS 詞性名稱表（與 DictModel.tag_names 獨立，以字串為對齊介面）。
    pub tag_names: Vec<String>,
    /// 初始機率，長度 = 狀態數。
    pub start: Vec<f32>,
    /// 一階轉移機率 dense [S*S]，索引 prev * S + cur。
    pub trans1: Vec<f32>,
    /// 二階轉移機率 dense [S*S*S]，索引 (prev2 * S + prev1) * S + cur。
    pub trans2: Vec<f32>,
    /// 發射 CSR 的字元表。
    pub chars: CharTable,
    /// CSR 列偏移，長度 = chars.len() + 1。
    pub emit_offsets: Vec<u32>,
    /// CSR：允許狀態 id（同一字元列內遞增排序）。
    pub emit_states: Vec<u16>,
    /// CSR：對應的發射 log 機率。
    pub emit_logps: Vec<f32>,
    /// 每個 joint-state 的 `<UNK>` 發射平滑機率。
    pub emit_unknown: Vec<f32>,
    /// 詞彙 POS 自動機；pattern value 為詞彙列 id。
    pub lexicon_automaton_bytes: Vec<u8>,
    /// 詞彙列 CSR 偏移，長度 = 詞彙數 + 1。
    pub lexicon_offsets: Vec<u32>,
    /// 詞彙列中的 POS tag id。
    pub lexicon_tags: Vec<u8>,
    /// 完整 log P(tag | word)。
    pub lexicon_logps: Vec<f32>,
}

impl PosModel {
    /// 取得某字元的（允許狀態, 發射機率）連續切片；字元不在表中回傳 None
    /// （呼叫端此時應退化為「所有狀態皆可、發射為地板值」）。
    #[inline]
    pub fn emit_row(&self, c: char) -> Option<(&[u16], &[f32])> {
        let i = self.chars.index_of(c)?;
        let lo = self.emit_offsets[i] as usize;
        let hi = self.emit_offsets[i + 1] as usize;
        Some((&self.emit_states[lo..hi], &self.emit_logps[lo..hi]))
    }
}

// ---------------------------------------------------------------------------
// 資產編解碼
// ---------------------------------------------------------------------------

/// 資產解碼錯誤。
#[derive(Debug)]
pub enum AssetError {
    /// 舊版 LXA1 資產；0.3.0 明確拒絕載入。
    LegacyLxa1,
    /// 檔頭 magic 不符或檔案過短。
    BadMagic,
    /// 版本不符（附實際讀到的版本）。
    BadVersion(u16),
    /// payload 的 xxh3 校驗失敗。
    BadHash,
    /// postcard 反序列化失敗。
    Decode(postcard::Error),
}

impl std::fmt::Display for AssetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AssetError::LegacyLxa1 => {
                write!(f, "LXA1 資產不相容；請以 0.3.0 converter 重建 LXA2")
            }
            AssetError::BadMagic => write!(f, "asset magic 不符（非 LXA2 資產檔）"),
            AssetError::BadVersion(v) => {
                write!(f, "asset 版本 {v} 與程式支援版本 {ASSET_VERSION} 不符")
            }
            AssetError::BadHash => write!(f, "asset 校驗和不符（檔案損毀）"),
            AssetError::Decode(e) => write!(f, "asset 反序列化失敗: {e}"),
        }
    }
}

impl std::error::Error for AssetError {}

/// 將模型編碼為帶檔頭的資產 bytes（僅離線轉換工具使用）。
pub fn encode_asset<T: Serialize>(value: &T) -> Vec<u8> {
    let payload = postcard::to_allocvec(value).expect("postcard 序列化不應失敗");
    let hash = xxhash_rust::xxh3::xxh3_64(&payload);
    let mut out = Vec::with_capacity(4 + 2 + 8 + payload.len());
    out.extend_from_slice(&ASSET_MAGIC);
    out.extend_from_slice(&ASSET_VERSION.to_le_bytes());
    out.extend_from_slice(&hash.to_le_bytes());
    out.extend_from_slice(&payload);
    out
}

/// 驗證檔頭並解碼資產。
pub fn decode_asset<T: DeserializeOwned>(bytes: &[u8]) -> Result<T, AssetError> {
    if bytes.len() >= 4 && bytes[0..4] == *b"LXA1" {
        return Err(AssetError::LegacyLxa1);
    }
    if bytes.len() < 14 || bytes[0..4] != ASSET_MAGIC {
        return Err(AssetError::BadMagic);
    }
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    if version != ASSET_VERSION {
        return Err(AssetError::BadVersion(version));
    }
    let stored_hash = u64::from_le_bytes(bytes[6..14].try_into().unwrap());
    let payload = &bytes[14..];
    if xxhash_rust::xxh3::xxh3_64(payload) != stored_hash {
        return Err(AssetError::BadHash);
    }
    postcard::from_bytes(payload).map_err(AssetError::Decode)
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lxa1_is_explicitly_rejected() {
        let error = decode_asset::<u8>(b"LXA1\x01\x00legacy").unwrap_err();
        assert!(matches!(error, AssetError::LegacyLxa1));
        assert!(error.to_string().contains("LXA1"));
    }
}
