# LingXi 0.4 摘要 API 遷移

0.4 將摘要從 `Vec<SummarySentence>` 升級為 schema v2 `SummaryDocument`，屬於刻意的 breaking change。

- Rust：`extract_summary(text, max_blocks)` 回傳 `SummaryDocument`；讀取 `document.text`、`document.blocks` 與 `document.budget`。
- Python：`extract_summary(text, max_blocks=3)` 回傳 `SummaryDocument`，不再回傳句子 list。
- WASM／C：摘要 JSON 由 array 改為 camelCase schema v2 object；C 函式名稱維持不變。
- CLI：`--summary N` 每份輸入輸出一個 schema v2 JSON；`N` 改為最大 prose block 數。
- Summary Lab：`/api/analyze` request 欄位由 `maxClauses` 改為 `maxBlocks`。

受保護的 code、list、table 與 HTML 不計入 `maxBlocks`；呼叫端若需硬限制輸出大小，應讀取 `budget.overflowReasons` 後自行拒絕或截斷，而不是假設輸出一定短於原文。
