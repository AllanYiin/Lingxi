# LingXi Summary Site

瀏覽器內執行的零 LLM 結構感知摘要頁面。內容不外送、不保存；LingXi WASM 與可散布的分詞／詞性模型在瀏覽器載入後，本機完成 POS 分析。POS 使用分片傳輸的 LXA3 i16 定點量化資產，合併解壓後還原為既有 f32 計算路徑。

摘要器先辨識 paragraph、heading、code、list、blockquote、table 與 HTML，再以 TextRank 與 LingXi POS signals 執行 block ranking。一般段落先受 `maxBlocks` 限制；入選段落超過 80 字且至少有 3 個可取捨單位時，再以逗號或句號結束的子句為單位擷取重點。頓號與密集名詞列舉整組不可分割；`Nb`／`Nc` 專名、數字與日期取得較高權重。最後以詞性移除不承載關鍵語意的副詞、連接詞與感嘆詞，否定、條件、因果、時間與程度限制則保留。

Rust core 與 TypeScript 版本共用 `tests/golden/summary-v2.json`，用相同輸入檢查 block kind、decision、signals 與輸出文字。

## 開發與驗證

```bash
npm install
npm run dev
npm test
```

需要 Node.js `>=22.13.0`。`npm test` 會先執行 Vinext production build，再驗證 rendered HTML、摘要行為與跨 runtime golden contract。
