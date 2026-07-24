//! 模型資料結構與二進位資產（asset）編解碼。
//!
//! 所有模型由 tools/lingxi-convert 離線從舊版 JSON 轉換；執行期只做
//! postcard 反序列化，零 JSON 解析。機率一律為 f32 log 域。
//!
//! 資產檔格式：4 bytes magic "LXA1" + u16 LE version + u64 LE xxh3(payload) + payload。

use serde::{de::DeserializeOwned, Deserialize, Serialize};

/// log 機率下限哨兵（對應舊版 C# 的 -3.14E+100），語意為「不可能」。
/// 不用 f32::NEG_INFINITY 是為了讓加法不產生 NaN（-inf + inf 等病態情況）。
pub const MIN_LOG: f32 = -1.0e30;

/// 資產檔頭 magic。
pub const ASSET_MAGIC: [u8; 4] = *b"LXA1";
/// 資產格式版本，結構有不相容變更時遞增。
pub const ASSET_VERSION: u16 = 1;

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
    /// ln(total_freq)：未登入單字的平滑基準（logp = ln(0.5) - total_log）。
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
    /// emit / r_emit 共用的字元表（各來源字元集聯集）。
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
    /// 首字狀態先驗 ln P(state | char)（由原始機率取 log；p=0 → MIN_LOG）。
    /// 字元無此統計時，轉換階段已填入 start 值，執行期無須分支。
    pub r_emit: Vec<[f32; 4]>,
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
    /// 轉移機率 dense [S*S]，索引 prev * S + cur。
    pub trans: Vec<f32>,
    /// 發射 CSR 的字元表。
    pub chars: CharTable,
    /// CSR 列偏移，長度 = chars.len() + 1。
    pub emit_offsets: Vec<u32>,
    /// CSR：允許狀態 id（同一字元列內遞增排序）。
    pub emit_states: Vec<u16>,
    /// CSR：對應的發射 log 機率。
    pub emit_logps: Vec<f32>,
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
            AssetError::BadMagic => write!(f, "asset magic 不符（非 LXA1 資產檔）"),
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
