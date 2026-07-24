//! lingxi-core：繁體中文分詞引擎核心。
//!
//! 管線：預切塊 → 詞典 DAG+DP → 二階 BMES HMM（未登入詞）→ POS Viterbi（OOV 詞性）。
//! 本 crate 只含演算法與模型載入，平行化與 I/O 由上層（CLI / bindings）負責。

pub mod chunk;
pub mod dag;
pub mod dict;
pub mod hmm;
pub mod model;
pub mod segment;

use std::path::Path;

use chunk::ChunkKind;
use dict::Dict;
pub use segment::{SegKind, Segment};

/// 分詞器：載入一次、多執行緒共享（`Send + Sync`，內部無可變狀態）。
pub struct Segmenter {
    dict: Dict,
    bmes: model::BmesModel,
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

/// 讀取並解碼單一資產檔。
fn load_asset<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, LoadError> {
    let bytes = std::fs::read(path).map_err(LoadError::Io)?;
    model::decode_asset(&bytes).map_err(LoadError::Asset)
}

impl Segmenter {
    /// 從資產目錄載入（需含 dict.bin 與 hmm_bmes.bin）。
    pub fn from_asset_dir(dir: impl AsRef<Path>) -> Result<Self, LoadError> {
        let dir = dir.as_ref();
        Ok(Segmenter {
            dict: Dict::from_model(load_asset(&dir.join("dict.bin"))?),
            bmes: load_asset(&dir.join("hmm_bmes.bin"))?,
        })
    }

    /// 分詞：回傳借用輸入的詞切片序列（零拷貝）。
    /// 空白、標點等一律保留為獨立詞段，由呼叫端自行過濾。
    pub fn cut<'a>(&self, text: &'a str) -> Vec<&'a str> {
        self.cut_segments(text)
            .into_iter()
            .map(|s| &text[s.byte_start..s.byte_end])
            .collect()
    }

    /// 分詞：回傳帶 byte 區間與種類的詞段。
    pub fn cut_segments(&self, text: &str) -> Vec<Segment> {
        let normalized = self.dict.normalize(text);
        let mut chunks = Vec::new();
        chunk::split(&normalized, &mut chunks);

        let mut out = Vec::with_capacity(text.len() / 4);
        let mut scratch: Vec<Segment> = Vec::new();
        for ch in &chunks {
            match ch.kind {
                ChunkKind::Han => {
                    scratch.clear();
                    dag::cut_dag(
                        &self.dict,
                        &normalized[ch.byte_start..ch.byte_end],
                        ch.byte_start,
                        &mut scratch,
                    );
                    self.merge_oov_runs(&normalized, &scratch, &mut out);
                }
                kind => out.push(Segment {
                    byte_start: ch.byte_start,
                    byte_end: ch.byte_end,
                    kind: direct_kind(kind),
                }),
            }
        }
        out
    }

    /// 對 DAG 輸出做 jieba 式未登入詞合併：連續 ≥2 個單字詞段
    /// （不論是否詞典單字）合併丟給二階 HMM 重切，其餘原樣輸出。
    fn merge_oov_runs(&self, normalized: &str, segs: &[Segment], out: &mut Vec<Segment>) {
        let is_single = |s: &Segment| {
            let t = &normalized[s.byte_start..s.byte_end];
            t.chars().next().map(|c| c.len_utf8() == t.len()).unwrap_or(false)
        };
        let mut run_start = 0usize; // 進行中單字 run 的起始索引
        let mut run_len = 0usize;
        for (i, seg) in segs.iter().enumerate() {
            if is_single(seg) {
                if run_len == 0 {
                    run_start = i;
                }
                run_len += 1;
            } else {
                self.flush_run(normalized, &segs[run_start..run_start + run_len], out);
                run_len = 0;
                out.push(*seg);
            }
        }
        self.flush_run(normalized, &segs[run_start..run_start + run_len], out);
    }

    /// 收尾一段單字 run：長度 ≥2 走 HMM；恰為詞典整詞時尊重 DP 的逐字選擇。
    fn flush_run(&self, normalized: &str, run: &[Segment], out: &mut Vec<Segment>) {
        match run.len() {
            0 => {}
            1 => out.push(run[0]),
            _ => {
                let a = run[0].byte_start;
                let b = run[run.len() - 1].byte_end;
                let run_str = &normalized[a..b];
                // 整串本身是詞典詞 → DP 刻意選了逐字路徑，不再合併。
                let whole_is_word = self
                    .dict
                    .matches(run_str)
                    .any(|m| m.byte_start == 0 && m.byte_end == run_str.len());
                if whole_is_word {
                    out.extend_from_slice(run);
                } else {
                    hmm::viterbi_cut(&self.bmes, run_str, a, out);
                }
            }
        }
    }
}

/// 預切塊種類 → 詞段種類（Han 不在此列，另走 DAG）。
fn direct_kind(kind: ChunkKind) -> SegKind {
    match kind {
        ChunkKind::Url => SegKind::Url,
        ChunkKind::Email => SegKind::Email,
        ChunkKind::Eng => SegKind::Eng,
        ChunkKind::Num => SegKind::Num,
        ChunkKind::Time => SegKind::Time,
        ChunkKind::Punct => SegKind::Punct,
        ChunkKind::Space => SegKind::Space,
        ChunkKind::Other => SegKind::Other,
        ChunkKind::Han => unreachable!("Han chunk 走 DAG 分支"),
    }
}
