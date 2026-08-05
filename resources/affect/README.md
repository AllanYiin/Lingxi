# LingXi 詞級情感資產

此目錄的來源檔與主詞頻模型完全分離：

- `emotion-taxonomy.json`：schema 1、taxonomy 版本與階層標籤定義。
- `emotion-lexicon.json`：人工維護的詞—多標籤關聯。
- `assets/affect.bin`：由 `lingxi-convert` 產生的 runtime 衍生檔，不是編輯來源。

## 資料原則

目前提交的詞條是專案人工種子資料，可依 repository 授權再散布。NRC、GoEmotions、ANTUSD 與中文研究只用於分類設計參考，未自動複製外部詞表；新增資料必須記錄 `source`，授權不明的詞條不得提交。

v1 僅提供詞級提示，不處理句級分類、否定作用域、反諷、上下文消歧或情緒強度。`Fake`、`Dangerous` 等非情緒概念存入 `semanticFlags`，`Blame` 存入 `appraisals`。

## 轉換

```powershell
cargo run -p lingxi-convert -- <舊模型目錄> assets resources/affect
```

第三個參數省略時預設為 `resources/affect`。轉換器會驗證 schema、taxonomy 版本、未知／棄用標籤，再輸出 `affect.bin`。

## 遷移

舊 18 種 emotion code 的映射定義在 `apps/dict-editor/core.js`。執行 `npm run migrate:emotions -- --dictionary ... --output ...` 只會寫入明確指定的新路徑，原始 `Dict.json` 不會被修改；不能一對一判定者列入 pending 檔供人工確認。
