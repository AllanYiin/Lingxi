# 模型資產與授權界線

## Goal｜目標

此 repository 目前發布原始碼，不發布可執行完整分詞所需的模型檔。`assets/dict.bin`、`assets/hmm_bmes.bin`、`assets/hmm_pos.bin` 與任何內嵌它們的 wheel、WASM bundle、ZIP 或內部交付包都不得提交到公開 GitHub repository。

原因是目前本機模型組合至少仍包含由受限語料訓練的 POS 參數；原始碼的 MIT License 不會覆蓋模型、訓練語料或其衍生統計。

這是專案層級的發布防線，不是法律意見。未來若更換模型來源，必須重新記錄 provenance 並審查各來源條款。

## 執行期需要的檔案

```text
assets/
├── dict.bin
├── hmm_bmes.bin
├── hmm_pos.bin
└── affect.bin       # 可選
```

- `dict.bin`：僅含多字詞的詞典、自動機與正頻率
- `hmm_bmes.bin`：LXA2 二階 BMES 發射／轉移與 `<UNK>` 平滑
- `hmm_pos.bin`：LXA2 二階 fixed-boundary POS 與完整詞彙 `P(tag|word)`
- `affect.bin`：由人工維護、可再散布的 taxonomy 與情感詞典轉換；缺少時不影響分詞與 POS

檔案格式由 `lingxi-core` 定義，`tools/lingxi-convert` 負責從本機 JSON 資料轉換。

## 不得公開提交的內容

- `assets/dict.bin`、`assets/hmm_bmes.bin`、`assets/hmm_pos.bin`
- `assets/affect.bin`（可由已提交來源重建；仍視為 generated artifact，不直接提交）
- `crates/lingxi-py/python/lingxi/assets/`
- `crates/lingxi-wasm/pkg/`
- `dist/`
- `*.whl`、`*.wasm`、內部 ZIP 或包含模型的其他封裝
- 外層舊專案的 `ModelingData/`、`ModelingData2/`、Resources 或訓練語料

`.gitignore` 已覆蓋這些主要路徑，但提交者仍須檢查 staged diff。

## Prerequisites｜前置條件

- 合法取得且可供本機使用的三個模型檔
- Rust stable toolchain
- 若要建 wheel：Python 3.9+ 與 maturin

## Procedure｜本機使用

### Step 1：準備模型

內部維護者可把合法取得的三個模型放在 `assets/`。下列命令會執行包含真實模型的整合測試：

```bash
cargo test --workspace --locked
```

若資產不存在，模型相關測試會跳過，核心純單元測試仍會執行。

### Step 2：驗證或封裝

Python wheel 建置會把本機資產複製到 package：

```bash
python tools/build_wheel.py
```

因此產出的 wheel 也視為內部資產，不得在目前狀態下公開發布。

## Verify｜Git 發布前檢查

```bash
git status --short --ignored
git ls-files
git diff --cached --stat
```

確認 staged files 不含上述模型、語料與產物。若曾經誤加入 Git 歷史，單純新增 `.gitignore` 不會移除歷史內容，必須先停止發布並清理 Git history。

## Troubleshooting｜故障排除

- 若模型出現在 staged files，先停止 commit，移除 staged 狀態並確認 `.gitignore` 命中。
- 若模型曾進入 Git 歷史，新增 ignore 規則不足以補救；停止發布並先清理 history。
- 若無資產的測試不是「跳過」而是失敗，檢查是否新增了未受保護的模型依賴測試。

## 未來解除限制的條件

至少需要完成：

1. 以可再散布來源重建所有模型。
2. 建立可重現的訓練與轉換流程。
3. 記錄每個來源、版本、授權、轉換步驟與輸出雜湊。
4. 重跑黃金集、語料不變量、效能與四種 binding 驗證。
5. 經發布者確認後，才調整 `.gitignore` 與發行政策。
