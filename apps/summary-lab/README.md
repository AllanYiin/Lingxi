# LingXi Summary Lab

完全在本機執行的抽取式摘要測試工具。Web server 只呼叫編譯後的 `lingxi` CLI；CLI 使用 LingXi core 與 `o200k_base` tiktoken tokenizer，不連接或呼叫任何 LLM。

工具先解析段落、標題、程式碼、清單、引用、表格與 HTML。程式碼、表格、HTML 與短清單項完整保留；長清單項只摘要 prose。專名、日期、金額與數字皆為軟加權，不會繞過 `maxBlocks`；已入選內容中的有效否定句則強制保留並在報告列出超額原因。中英文誤判詞如 `非常`、`是否`、`未來`、`否則`、`not only` 與 `whether or not` 不計為否定。

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
cargo build --release -p lingxi-cli
cd apps\summary-lab
npm run dev
```

開啟 `http://127.0.0.1:4174`。可用環境變數調整：

- `PORT`：server port，預設 `4174`
- `LINGXI_BIN`：`lingxi` executable 絕對路徑
- `LINGXI_ASSETS`：模型資產目錄

預設優先使用 `target/release/lingxi`，避免 debug build 每次分析都承擔較高的資產解碼成本；若 release binary 尚不存在，開發環境仍可退回既有 debug binary。

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
