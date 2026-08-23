# Token 節省手段清單

## A. 不損失資訊的 deterministic 減量

1. **Canonical serialization**：固定欄位順序、空白與 Unicode normalization。
2. **Exact content dedup**：使用 content hash 去除重複規則與附件片段。
3. **Tool schema projection**：只暴露 route 所需工具。
4. **Path compaction**：重複 prefix 只宣告一次。

## B. 結構式裁切

- 保留 tool call identity。
- 保留日期 2026-08-20。
- 保留 token budget 128k。
- 不要刪除 `prompt_cache_options.mode="explicit"`。
