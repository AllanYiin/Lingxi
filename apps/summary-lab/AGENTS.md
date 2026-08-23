# AGENTS.md

## 1. 專案定位

- LingXi Summary Lab 是提供少量使用者共用的本機零 LLM 摘要驗證工作台。
- 使用情境為 `scene_b_shared_tool`；維護時以不懂命令列的使用者為基準。
- 技術組合為單一 Node fullstack service，加上 Rust CLI 計算核心；`project.config.json` 是 APSM 單一真相來源。

## 2. 最高原則

- Windows 10/11 優先，雙擊 `run_app.bat` 必須能安裝專案依賴、建置並啟動。
- macOS/Linux 保留 `run_app.command`／`run_app.sh`，不得另建平行主入口。
- `run_app.*` 一律由 `scripts/project_launcher.py` 生成或維護。
- 不呼叫任何 LLM、不生成或改寫摘要、不將輸入傳到 localhost 以外。
- 修改採最小改動；不可為了 launcher 重做既有 UI 或摘要演算法。

## 3. 目錄與檔案

- `specs/requirements.md` 是需求真相；新需求先更新它，再更新 `todo.md`。
- `docs/design-token-board.md` 是 canonical token spec；視覺變更必須對齊。
- `.runtime/` 與 `logs/` 由 launcher 管理；不得寫入使用者原文、秘密或 API key。
- `.env` 不進版控；可公開預設值放在 `.env.example`／`.launcher.env`。

## 4. 實作規範

- server 只綁定 loopback，browser 只連同源 API。
- Rust CLI 及 `lingxi-core` 是摘要與 tiktoken 報告的單一真相來源。
- Python 檔案以 UTF-8、`pathlib` 與標準函式庫為主；`.bat` 維持 ASCII-only 且不得使用 POSIX 指令。
- port 4174 衝突時清楚失敗，不宣稱自動遞補；若未來做動態 port，實際 bind 結果必須同步 URL、API、logs 與 runtime metadata。

## 5. UI / UX 規範

- 唯一主任務：貼入內容、按「執行」、取得可稽核摘要。
- 先維護 task model、state model、資訊分類、visibility plan 與 content audit，才可改版。
- 主任務佔最大、中央與第一視線；首屏最多 2–3 個主要群組且只有一個主 CTA。
- `reference` 預設放 disclosure/tab；`exception-handling` 僅在 error state 顯示。
- 禁止 stacked UI、stacked cards、dashboard card farm；空狀態必須說明下一步。
- deferred block 必須在規格記錄隱藏理由、揭露事件與容器。

## 6. 修改規範

- 不任意變更 `report` JSON 欄位、`/api/analyze`、CLI `--summary-report` 或 launcher 公開檔名。
- 啟動流程、port、依賴或 UI visibility 改變時，同步更新 README、requirements、todo 與測試。
- 保留工作區中與本任務無關的既有變更。

## 7. 測試與打包

- 必跑 Node tests、Rust targeted tests、workspace check、launcher syntax、實際 readiness probe。
- 交付前執行 `python scripts/apsm_validate.py --project . --strict`。
- 打包由 `python scripts/project_launcher.py --package` 產生；不得手工拼 ZIP。
- 只有實際通過的 Windows/macOS/Linux 平台才可標為 PASS；未實測平台需明示限制。

## 8. 專案特例

- `src/web/package.json` 是 APSM single-service contract adapter；實際可見資產仍由根目錄 `server.mjs` 服務。
- Python launcher 沒有第三方依賴；`requirements.txt` 應保持最小。
- 系統級 Python、Node.js 與 Rust/Cargo 不得在未取得使用者同意時靜默安裝。
