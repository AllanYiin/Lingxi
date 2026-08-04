# Contributing to LingXi

感謝你協助改進 LingXi。此專案目前接受原始碼、測試與文件變更，但不接受模型、語料或內部建置產物。

## Goal｜目標

讓每個變更都能由公開原始碼重現、由測試驗證，並維持模型與語料的發布界線。

## Prerequisites｜開發環境

- Git
- Rust stable
- 選用：Python 3.9+ 與 maturin（Python binding）
- 選用：wasm-pack（WASM binding）

## 建立開發環境

```bash
git clone <repository-url>
cd lingxi-rs
cargo test --workspace --locked
```

沒有 `assets/*.bin` 時，模型型整合測試會跳過；這是公開貢獻者的正常路徑。

## Procedure｜修改與驗證

### Step 1：執行基礎檢查

提交前至少執行：

```bash
cargo fmt --all
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

### Step 2：補足變更類型的驗證

如果修改分詞邊界：

1. 在 `crates/lingxi-core/src/chunk.rs` 或對應模組加入不依賴模型的單元測試。
2. 若行為應成為產品不變量，再更新 `tests/golden/must_pass.tsv`。
3. 有合法本機模型時，重跑完整 workspace 測試並確認黃金案例通過。

如果修改 binding，至少編譯對應 crate；資產封裝只在內部環境驗證。

## 資產與資料規則

請先閱讀 [ASSETS.md](ASSETS.md)。禁止提交：

- 模型 `.bin`
- 訓練語料或外層 `ModelingData*`
- wheel、WASM bundle、ZIP 與 `dist/`
- 含上述內容的測試 fixture

## Verify｜提交前檢查

```bash
git status --short --ignored
git diff --cached --stat
```

## Troubleshooting｜故障排除

- `--locked` 失敗：確認 `Cargo.lock` 已更新且未被 ignore。
- 無資產時整合測試失敗：確認測試沿用現有的 assets-optional 載入模式。
- `git status --ignored` 未列出模型：先修正 `.gitignore`，不要繼續 stage。

## Pull request 建議

PR 說明請包含：

- 問題與預期行為
- 實作範圍
- 新增或更新的測試
- 實際執行的驗證命令
- 是否影響模型格式、資產來源或發布邊界

保持每個 PR 聚焦；不要把機械格式化與行為變更混在同一個 commit。
