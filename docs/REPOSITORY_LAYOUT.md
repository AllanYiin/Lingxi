# Repository layout and publish checklist

## Goal｜目標

此文件說明公開 GitHub repository 的邊界。repo 根目錄應是目前的 `lingxi-rs/`，不是包含舊 C# solution、訓練資料與 `DEVNOTE.md` 的外層工作目錄。

## Prerequisites｜前置條件

- 從 `lingxi-rs/` 執行命令
- Git 與 Rust stable 已安裝
- 尚未 stage 或 push 任何模型與內部產物

## 目錄

```text
lingxi-rs/
├── .github/
│   └── workflows/          # GitHub Actions
├── assets/                 # 本機模型；只追蹤 README
├── crates/
│   ├── lingxi-core/        # 核心演算法
│   ├── lingxi-cli/         # CLI
│   ├── lingxi-py/          # Python binding
│   ├── lingxi-wasm/        # WASM binding
│   └── lingxi-ffi/         # C ABI
├── docs/                   # 維護與發布文件
├── tests/
│   └── golden/             # 分詞邊界不變量
├── tools/
│   ├── build_wheel.py      # 內部 wheel 建置
│   └── lingxi-convert/     # 模型轉換工具
├── ASSETS.md
├── CONTRIBUTING.md
├── Cargo.lock
├── Cargo.toml
├── LICENSE
└── README.md
```

`target/`、`dist/`、`crates/lingxi-wasm/pkg/` 與 Python package 內嵌資產是本機產物，不屬於 repository 結構。

## 為什麼不搬 crate

Cargo workspace 已以 `crates/*` 與 `tools/lingxi-convert` 清楚分層。把 binding 或轉換器移到新的巢狀目錄只會改壞 path dependency、建置腳本與文件，沒有 GitHub 發布價值，因此本次保留既有 crate 路徑。

## Procedure｜發布前檢查

### Step 1：確認 repository 根目錄

```bash
git rev-parse --show-toplevel
```

輸出應指向 `lingxi-rs`。

### Step 2：確認工作樹

```bash
git status --short --ignored
git diff --check
```

允許出現在 ignored 區的項目包括 `target/`、`dist/` 與本機 assets；它們不可出現在 staged files。

### Step 3：確認追蹤內容

```bash
git ls-files
git diff --cached --stat
```

逐項確認沒有模型、語料、wheel、WASM binary 或內部 ZIP。

### Step 4：驗證原始碼

```bash
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

## Verify｜驗證結果

以下條件全部成立才算完成發布前整理：格式、測試與 Clippy 通過；staged files 不含資產；repo 根目錄正確；README 與 LICENSE 可見。

## GitHub 設定

建立 remote 後再由 repository owner 設定：

- 預設分支與 branch protection
- CI 為必要檢查
- Issues／Discussions 是否啟用
- release 權限與秘密管理
- repository URL、description 與 topics

本機目前沒有 remote，因此文件不假設任何 GitHub owner、URL 或 release destination。

## Troubleshooting｜故障排除

- repo 根目錄指向外層 `LingXi/`：不要發布；切回巢狀 `lingxi-rs/`。
- staged files 出現 `dist/`、`assets/*.bin` 或 ZIP：先取消 staged 並檢查 ignore 規則。
- CI 在格式檢查失敗：本機執行 `cargo fmt --all` 後重新驗證。
- CI 在 `--locked` 失敗：更新並提交 `Cargo.lock`。
