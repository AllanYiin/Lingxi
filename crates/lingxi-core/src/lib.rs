//! lingxi-core：繁體中文分詞引擎核心。
//!
//! 管線：預切塊 → 詞典 DAG+DP → 二階 BMES HMM（未登入詞）→ POS Viterbi（OOV 詞性）。
//! 本 crate 只含演算法與模型載入，平行化與 I/O 由上層（CLI / bindings）負責。

pub mod model;
