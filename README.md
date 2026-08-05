# LingXi

## Overview｜專案概覽

LingXi 是以 Rust 重寫的繁體中文（台灣語料）分詞與詞性標註引擎。專案以單一核心提供 CLI、Python、WASM/JavaScript 與 C ABI，並支援自訂詞典及 TextRank 關鍵字抽取。

> [!IMPORTANT]
> 此 repository 目前只適合公開原始碼。執行完整分詞所需的本機模型不隨 repository 發布；現有模型組合至少包含不可公開再散布的語料衍生資產。請勿提交 `assets/*.bin`、wheel、WASM bundle 或 `dist/` 內部交付包。詳見 [ASSETS.md](ASSETS.md)。

## 功能

- 單一全域二階 BMES Viterbi；多字詞典命中替代區段內 BMES 分數
- 單字完全由 BMES 決定；分詞詞典只含有限正頻率的多字詞
- 固定詞界的全句二階 POS Viterbi；已知詞使用完整 `P(tag|word)`
- URL、Email、英數、時間、百分比與數量級預切
- runtime 自訂詞典（多字詞＋必填有限正頻率）
- TextRank 關鍵字抽取
- CLI、Python、WASM/JavaScript、C ABI 四種介面

## 快速開始：驗證公開原始碼

### Prerequisites / Requirements｜前置條件

- Rust stable toolchain
- Git

```bash
git clone <repository-url>
cd lingxi-rs
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

沒有模型資產時，純核心單元測試仍會執行；需要真實模型的整合測試會明確顯示「assets 不存在，跳過」。兩個命令皆成功結束即代表公開原始碼可建置。

## Installation｜安裝與本機模型

完整執行需要以下三個檔案：

```text
assets/
├── dict.bin
├── hmm_bmes.bin
└── hmm_pos.bin
```

這些檔案目前不在公開 repository 中。若你是內部維護者，請依 [ASSETS.md](ASSETS.md) 的規則準備本機資產；一般貢獻者不需要模型也能修改及測試純核心邏輯。

## Usage｜使用方式

### CLI

有模型後可執行：

```bash
cargo build --release -p lingxi-cli
echo "市值縮水約1200億美元" | ./target/release/lingxi --format tsv
```

0.3.0 模型與各 binding 的 `tag` 欄位使用 CKIP 原生詞性代碼。POS 固定在分詞完成後執行，不參與詞界競爭；舊 POS rerank 參數已移除。

### Python

需要 Python 3.9+、maturin，以及可合法使用的本機模型：

```bash
python tools/build_wheel.py
```

```python
import lingxi

seg = lingxi.load()
seg.cut("金管會前主委參加記者會")
seg.tokenize("市值縮水約1200億美元")
seg.cut_batch(["第一句", "第二句"])

seg = lingxi.load(
    user_dict=["板南線 100000 Nb", "柯文哲 100000 Nb", "鹽酥雞 100000 Na"]
)

# TextRank 預設另以 Nb 建立專有名詞通道：
# 依詞頻與首次出現位置提供軟加分，不保證入榜；可調整權重與占比上限。
seg.extract_keywords("要分析的長文本", top_k=20)
seg.extract_keywords(
    "要分析的長文本",
    top_k=20,
    proper_noun_enabled=True,
    proper_noun_weight=0.25,
    proper_noun_max_ratio=0.4,
)

```

### WASM 與 C ABI

WASM binding 位於 `crates/lingxi-wasm`，模型由 JavaScript 載入後傳入：

```bash
cd crates/lingxi-wasm
wasm-pack build --release --target web
```

C ABI 位於 `crates/lingxi-ffi`，公開標頭為 `crates/lingxi-ffi/include/lingxi.h`：

```bash
cargo build --release -p lingxi-ffi
```

## 演算法管線

```text
文字
  → 正規化（ASCII 小寫 + 異體字）
  → 確定性保護（URL / Email / 英數 / 數量 / 年份 / 時間 / 標點）
  → Han 塊建立所有 BMES 詞段與多字詞典命中索引
  → 單一全域二階 Viterbi（詞典 log(freq/total) 只替代命中區段內部）
  → 固定詞界的全句二階 POS Viterbi
     ├─ 已知詞：完整 P(tag|word)
     └─ OOV：字元 joint-state POS HMM
  → Token（原文區間 + 詞性）
```

## Repository 結構

| 路徑 | 用途 | 公開狀態 |
|---|---|---|
| `crates/lingxi-core` | 分詞、HMM、POS、自訂詞典、TextRank | 可公開 |
| `crates/lingxi-cli` | 命令列工具 | 可公開 |
| `crates/lingxi-py` | PyO3 + maturin Python binding | 原始碼可公開 |
| `crates/lingxi-wasm` | wasm-bindgen binding | 原始碼可公開 |
| `crates/lingxi-ffi` | C ABI 與標頭 | 可公開 |
| `tools/lingxi-convert` | 舊 JSON 模型轉二進位資產 | 工具原始碼可公開 |
| `tests/golden` | 必須通過的分詞邊界案例 | 可公開 |
| `assets` | 本機模型放置處 | 僅 README 可公開 |
| `dist` | 內部建置與交付產物 | 不可提交 |

完整說明見 [docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md)。

## Trident 全量測試：LingXi vs jieba

以下結果使用 Trident 0.7.12 `load_examples_data("chinese")` 的完整測試集：4,263 句、265,287 字，分詞 BMES gold 共 163,498 詞。測試於 2026-08-04 在 Windows x86_64（24 logical processors）、Python 3.10.10 與 rustc 1.96.0 上執行；LingXi 使用 0.3.0 release CLI 與本機 LXA2 模型，jieba 版本為 0.42.1。

| 模型 | 分詞正確性（Word F1） | 分詞執行時間 | 詞性正確性（共同 POS accuracy） | 詞性執行時間 | 詞界＋POS F1 |
|---|---:|---:|---:|---:|---:|
| jieba 0.42.1 | 74.76% | 765.93 ms | 69.89% | 199,887.94 ms | 52.21% |
| LingXi 0.3.0 | **86.34%** | **291.00 ms** | **93.80%** | **963.00 ms** | **79.79%** |

分詞正確性以 Trident BMES 詞界計算 micro Word F1。Trident 未提供本比較所需的 CKIP 詞性 gold，因此詞性評測使用同一批句子的 CKIPTagger WS＋POS silver annotations（164,109 tokens），並排除標點；「共同 POS accuracy」只計算 gold 與預測詞界完全對齊的實詞 token，再把 CKIP 與 jieba 標籤映射到共同 tag set。「詞界＋POS F1」則同時懲罰詞界和詞性錯誤，較能反映端到端結果。silver annotations 未經逐筆人工覆核，不應視為人工 gold benchmark。

兩種模式皆先暖機，再完整執行 3 次並取處理時間中位數；資料集與模型載入時間不計。分詞時間只含分詞；詞性時間為分詞＋POS 的端到端時間。LingXi 數字來自 release CLI 回報的處理時間（含輸出序列化），jieba 則在同一 Python 行程內以 `cut(cut_all=False, HMM=True)`／`posseg.cut(HMM=True)` 執行。不同硬體、模型資產與執行環境的時間不可直接互比。

內部維護者備妥 `assets/`、0.2.2 對照包與 `.corpus-work/model-evaluation/trident-test-ckip-gold.jsonl` 後，可重現本次報告：

```bash
python corpus/compare_022_030_jieba.py \
  --limit 4263 \
  --repeats 3 \
  --output-json .corpus-work/model-evaluation/jieba-lingxi-022-030-full-comparison.json \
  --output-markdown .corpus-work/model-evaluation/jieba-lingxi-022-030-full-comparison.md
```

## 效能基線

在 Windows 11 x86_64、0.3.0 LXA2 模型上，代表性繁中長文的單執行緒 cut Criterion 中位數為：

- 10K 字：14.18 ms
- 20K 字：27.90 ms
- 40K 字：48.71 ms
- 10K／20K／40K CLI 行程峰值工作集約 216.6–216.7 MiB；主要由 116.3 MB 模型資產主導
- 固定 1,400 句診斷的 CLI 模型載入約 194 ms、處理約 320 ms

數據會隨模型、硬體與編譯器版本改變，應以本機 benchmark 為準：

```bash
cargo bench -p lingxi-core
```

## 貢獻與授權

提交變更前請閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。原始碼採 [MIT License](LICENSE)；模型與語料不因原始碼採 MIT 而自動取得相同授權。
