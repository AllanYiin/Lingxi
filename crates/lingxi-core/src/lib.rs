//! lingxi-core：繁體中文分詞引擎核心。
//!
//! 管線：預切塊 → 詞典 DAG+DP → 二階 BMES HMM（未登入詞）→ POS Viterbi（OOV 詞性）。
//! 本 crate 只含演算法與模型載入，平行化與 I/O 由上層（CLI / bindings）負責。

pub mod dag;
pub mod dict;
pub mod model;

use std::path::Path;

use dag::Segment;
use dict::Dict;

/// 分詞器：載入一次、多執行緒共享（`Send + Sync`，內部無可變狀態）。
pub struct Segmenter {
    dict: Dict,
}

/// 模型載入錯誤。
#[derive(Debug)]
pub enum LoadError {
    Io(std::io::Error),
    Asset(model::AssetError),
}

impl std::fmt::Display for LoadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LoadError::Io(e) => write!(f, "讀取模型檔失敗: {e}"),
            LoadError::Asset(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for LoadError {}

impl Segmenter {
    /// 從資產目錄載入（目錄需含 dict.bin；HMM 資產於後續里程碑加入）。
    pub fn from_asset_dir(dir: impl AsRef<Path>) -> Result<Self, LoadError> {
        let dir = dir.as_ref();
        let dict_bytes = std::fs::read(dir.join("dict.bin")).map_err(LoadError::Io)?;
        let dict_model = model::decode_asset(&dict_bytes).map_err(LoadError::Asset)?;
        Ok(Segmenter { dict: Dict::from_model(dict_model) })
    }

    /// 分詞：回傳借用輸入的詞切片序列（零拷貝）。
    pub fn cut<'a>(&self, text: &'a str) -> Vec<&'a str> {
        self.cut_segments(text)
            .into_iter()
            .map(|s| &text[s.byte_start..s.byte_end])
            .collect()
    }

    /// 分詞：回傳帶 byte 區間的詞段（內部與進階用途）。
    pub fn cut_segments(&self, text: &str) -> Vec<Segment> {
        let normalized = self.dict.normalize(text);
        let mut out = Vec::with_capacity(text.len() / 4);
        // M1：整段直接走 DAG；預切塊（URL/英數/標點）於 M2 加入。
        dag::cut_dag(&self.dict, &normalized, 0, &mut out);
        out
    }
}
