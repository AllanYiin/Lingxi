# LingXi Wiki

LingXi 是以 Rust 實作、面向繁體中文與台灣語料的分詞及詞性標註引擎。它以同一套核心演算法提供 CLI、Python、WASM/JavaScript 與 C ABI，並延伸支援多領域自訂辭典、詞級情感標註及 TextRank 關鍵字抽取。

本 Wiki 適合：

- 想快速了解 LingXi 能解決哪些文字處理問題的使用者。
- 準備從 CLI、Python、瀏覽器或原生程式整合 LingXi 的開發者。
- 需要理解分詞、詞性標註與自訂辭典設計取捨的維護者。

## 核心能力

| 能力 | 說明 |
|---|---|
| 繁體中文分詞 | 結合確定性預切規則、主詞典與全域二階 BMES Viterbi，決定完整句子的詞界。 |
| 詞性標註 | 詞界確定後，再以全句二階 POS Viterbi 選擇詞性；詞性不會反過來改動分詞。 |
| 結構化文字保護 | URL、Email、英數、時間、百分比、數量級與標點會先被辨識，避免被中文模型錯切。 |
| 自訂辭典 | 可同時載入多份、帶領域與優先序的版本化 JSON 辭典，也保留舊 runtime 詞典相容性。 |
| 詞級情感資料 | `annotate` 可附加極性、多個情緒標籤、語意旗標與辭典來源；不宣稱句級情緒推論。 |
| 關鍵字抽取 | 以 TextRank 建立候選詞共現圖，並可加入有界的專有名詞軟加分。 |
| 多語言介面 | Rust 核心向上提供 CLI、Python、WASM/JavaScript 與 C ABI。 |

## 文件導覽

- [功能介紹](Feature-Overview)：能力、適用情境、介面與已知限制。
- [核心原理](How-LingXi-Works)：從輸入正規化到分詞、詞性、情感與關鍵字抽取的完整資料流。
- [發布至 GitHub Wiki](Publishing-the-Wiki)：首次建立、更新、驗證與回退方式。
- [主專案 README](https://github.com/AllanYiin/Lingxi)：安裝、使用範例、效能基線與 repository 結構。

## Quick Start / Usage｜快速開始與使用方式

### Prerequisites / Requirements｜前置條件

- Rust stable toolchain
- Git

### 驗證公開原始碼

此 repository 不含完整分詞所需的模型資產，但可先驗證原始碼：

```bash
git clone https://github.com/AllanYiin/Lingxi.git
cd Lingxi
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

### 使用完整模型

內部維護者備妥 `assets/dict.bin`、`assets/hmm_bmes.bin` 與 `assets/hmm_pos.bin` 後，可建置 CLI：

```bash
cargo build --release -p lingxi-cli
echo "市值縮水約1200億美元" | ./target/release/lingxi --format tsv
```

## 重要界線

> 完整模型、語料衍生資產、內部 wheel、WASM bundle 與 `dist/` 交付包不屬於目前可公開發布範圍。Wiki 只說明行為與介面，不提供或重新散布這些資產。詳見專案的 [ASSETS.md](https://github.com/AllanYiin/Lingxi/blob/master/ASSETS.md)。

本 Wiki 目前對應 LingXi `0.3.0`；功能與介面以同版本原始碼為準。
