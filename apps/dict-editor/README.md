# LingXi Lexicon Studio

以瀏覽器維護 LingXi 內建詞典、多領域自訂辭典、情感詞典與分詞訓練資料的本機 Web App，不會把檔案上傳到伺服器。

## 啟動

```powershell
cd apps\dict-editor
npm run dev
```

接著開啟 `http://127.0.0.1:4173`。

## 支援功能

- 開啟、驗證、搜尋與分頁顯示大型 `Dict.json`
- 內建詞典的詞頻唯讀並由批次作業維護；情感資料不再寫回 `Dict.json`
- 建立、開啟與驗證無詞頻的領域自訂辭典（id、domain、priority、enabled）
- 以家族樹狀多選器維護情感詞，並依家族、細標籤、極性、未標註或待確認篩選
- 複選刪除、批次標記為 `unknown`、清除篩選
- 全域詞語字串代換，執行前顯示影響筆數與衝突
- Chromium 瀏覽器可直接寫回已授權檔案；其他瀏覽器下載新檔
- 訓練資料 TXT 的隨機瀏覽、查詢、代換、刪除與潛在新詞匯出

## 詞典格式

與舊版 `LingXi.Models.DictBase` 相容：

```json
{
  "台積電": ["nt", 100000],
  "開心": ["a", 1200, "Happy"],
  "王小明": ["nr", 30, "ChName"],
  "流行語": ["n", 50, "Catchword", "Happy"]
}
```

第三欄會依舊版 enum 自動判斷是 Entity 或 Emotion；同時具有兩者時使用四欄格式。

## 新格式與情感遷移

自訂辭典是一檔一領域的 JSON，`frequency` 會被拒絕；同時使用多份檔案是在建立 Segmenter 時由 CLI、Python 或其他 binding 指定。情感工作模式載入同目錄的 `emotion-taxonomy.json`，詞條可複選細標籤，極性依 taxonomy 推導。

載入舊 `Dict.json` 後可按「預覽情感遷移」。Studio 只下載 `emotion-lexicon.migration-preview.json` 與必要時的 `emotion-migration.pending.json`，不會覆寫原檔。命令列也可明確指定輸出：

```powershell
npm run migrate:emotions -- `
  --dictionary D:\path\to\Dict.json `
  --output D:\path\to\emotion-lexicon.preview.json `
  --pending D:\path\to\emotion-migration.pending.json
```

`--output` 必填，且不得與輸入路徑相同；無法映射的舊代碼會進入人工確認清單。

## 驗證

```powershell
npm test
```

## 詞頻批次

詞頻採兩階段取樣，並使用與 LingXi 一致的正規化口徑（目前包含 `臺 → 台` 與 ASCII 小寫化）：先暫時移除 CKIP 語料的 `|` 分隔符號，找出包含待計算詞語的候選句；再以「相同詞性、既有正詞頻的中位數」作為 provisional user dictionary 詞頻，使用同一份 LingXi assets 重新分詞。只有模型輸出仍保留完整 canonical token 的出現才納入計數，最後將三個來源加總。

`台積電`、`臺積電` 這類異體表面詞會歸到同一 canonical 詞形，共用候選句、取樣狀態與最終詞頻；若詞典同時保留兩個寫法，批次會將相同結果回寫到所有 alias。中位數只用來協助重新分詞，不會直接當成最終詞頻。新增詞條固定先寫入預設詞頻 `1`；批次計算完成後會記錄在獨立狀態檔，之後不再修改該詞條的詞頻。

執行批次前，先在專案根目錄建置 release CLI：

```powershell
cargo build --release -p lingxi-cli
```

首次啟用時，先將既有詞條登記為已計算，避免全量覆寫：

```powershell
npm run frequency -- --dictionary D:\path\to\Dict.json --bootstrap-existing
```

之後可由排程定期執行；預設待計算詞條達 20 筆才掃描三份語料：

```powershell
npm run frequency -- --dictionary D:\path\to\Dict.json
```

常用選項：

- `--threshold 50`：自訂累積門檻。
- `--force`：尚未達門檻也執行。
- `--dry-run`：完成掃描與計算，但不回寫。
- `--corpus <path>`：自訂語料位置；每個來源各傳一次。
- `--state <path>`：自訂狀態檔位置。未指定時使用 `Dict.json.frequency-state.json`。
- `--segmenter <path>`：自訂 LingXi release CLI 位置。
- `--assets <path>`：自訂模型 assets 目錄，確保與正式模型使用相同資料。

每個來源都會輸出候選句數、原始命中數、原 CKIP 完整 token 數、重新分詞接受數與拒絕數，方便稽核切詞差異。批次使用鎖檔避免重複執行，且以暫存檔取代方式回寫。狀態檔以 canonical 詞形記錄，是「只計算一次」的依據，必須和詞典一起備份與部署。
設計與元件規範見 [DESIGN.md](./DESIGN.md)。
