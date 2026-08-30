# Local model assets

此目錄放置 LingXi 模型；是否可散布以 [../ASSETS.md](../ASSETS.md) 的 provenance 與核准雜湊為準。

需要的檔案為 `dict.bin`、`hmm_bmes.bin`、`hmm_pos.bin`。其中 POS 預設採 LXA3 i16 定點量化格式，runtime 仍相容既有 LXA2 f32 資產。現行核准版本可隨網站、binding 或 release 散布；舊版或雜湊不符的模型不得發布。詳細 provenance、測試方式與發布檢查請見 [../ASSETS.md](../ASSETS.md)。
