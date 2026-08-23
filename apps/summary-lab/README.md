# LingXi Summary Lab

完全在本機執行的抽取式摘要測試工具。Web server 只呼叫編譯後的 `lingxi` CLI；CLI 使用 LingXi core 與 `o200k_base` tiktoken tokenizer，不連接或呼叫任何 LLM。

條列、日期與數字會被視為重要事實保留，帶單位數值有額外權重；括號或引號中的全大寫縮略語（如 `FOMO`）也會硬保留。若輸入幾乎全是 Markdown 標題與條列，工具會判定它已是高密度重點筆記，完整保留原文並在報告中明示，不再進行二次簡化。`不只`／`不僅` 不會被誤標成否定；條件或數值前件也會與後果一起抽取。

## 啟動

### 一鍵安裝啟動（建議）

- Windows：雙擊 `run_app.bat`
- macOS：雙擊 `run_app.command`；若沒有執行權限，先執行 `chmod +x run_app.command`
- Linux：雙擊或執行 `run_app.sh`；若沒有執行權限，先執行 `chmod +x run_app.sh`

啟動器會自動建立 `.venv`、檢查 Python／Node.js／Cargo、執行 `npm install`、建置本機 `lingxi` CLI、等待 `http://127.0.0.1:4174` 可連線後開啟瀏覽器。失敗時請查看 `logs/ensure.log`、`logs/frontend.log` 與 `logs/launcher.log`。

系統工具需求：Python 3.10+、Node.js 20+、Rust stable／Cargo。啟動器會安裝專案依賴，但不會在未經同意下修改系統或代替使用者安裝系統級工具。

若 Windows SmartScreen 阻擋批次檔，可確認檔案來源後按「其他資訊」→「仍要執行」。若 `4174` 已被其他程式占用，啟動器會停止並提示查看 logs，不會假裝已切換到其他 port。

### 開發者手動啟動

```powershell
cargo build -p lingxi-cli
cd apps\summary-lab
npm run dev
```

開啟 `http://127.0.0.1:4174`。可用環境變數調整：

- `PORT`：server port，預設 `4174`
- `LINGXI_BIN`：`lingxi` executable 絕對路徑
- `LINGXI_ASSETS`：模型資產目錄

## 零 LLM 邊界

- browser 只連到同源 `/api/analyze`。
- server 的 Content Security Policy 將 `connect-src` 限制為 `'self'`。
- server 只啟動本機 `lingxi` executable，不包含 OpenAI、Anthropic 或其他生成 API client。
- token 數是 `o200k_base` tiktoken 計數；它是 tokenizer 計算，不是模型推論。

## 驗證

```powershell
npm test
python scripts/apsm_validate.py --project . --strict
cargo test -p lingxi-core --lib --test features
cargo check --workspace
```

## 專案交付結構

- `run_app.bat`、`run_app.command`、`run_app.sh`：跨平台一鍵入口，由 `scripts/project_launcher.py` 生成與維護。
- `project.config.json`：使用情境與 APSM 選型的單一機器可讀設定。
- `specs/requirements.md`：功能、工作台資訊架構與啟動驗收條件。
- `.runtime/`：啟動器產生的 port 與狀態 metadata。
- `logs/`：安裝、建置、server 與啟動錯誤記錄。
