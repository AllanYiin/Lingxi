# LingXi

## Overview｜專案概覽

LingXi 是以 Rust 重寫的繁體中文（台灣語料）分詞與詞性標註引擎。專案以單一核心提供 CLI、Python、WASM/JavaScript 與 C ABI，並支援自訂詞典、TextRank 關鍵字／關鍵短語及抽取式摘要。

> [!IMPORTANT]
> 現行模型由維護者自有文本、中研院分詞結果與人工校閱建立；與 [ASSETS.md](ASSETS.md) 核准雜湊吻合的版本可隨 binding、網站與 release 散布。舊版或來源／雜湊未經確認的模型仍不得發布。

## 功能

- 單一全域二階 BMES Viterbi；多字詞典命中替代區段內 BMES 分數
- 單字完全由 BMES 決定；分詞詞典只含有限正頻率的多字詞
- 固定詞界的全句二階 POS Viterbi；已知詞使用完整 `P(tag|word)`
- URL、Email、英數、時間、百分比與數量級預切
- 相容舊 runtime 自訂詞典（多字詞＋有限正頻率）
- 多份、可分領域、無詞頻的版本化自訂辭典；不改變主詞典總頻率
- 獨立情感 taxonomy 與詞級多標籤 `annotate`（不做句級情緒推論）
- TextRank 關鍵字抽取
- 中文句界與原文 offset
- schema v2 結構感知摘要（block-first、BM25／詞面相似度、去冗餘）
- 相鄰關鍵詞組成的關鍵短語
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

完整分詞需要三個模型檔，另可選擇載入詞級情感資產：

```text
assets/
├── dict.bin
├── hmm_bmes.bin
├── hmm_pos.bin
└── affect.bin       # 可選；缺少時 annotate 的 affect 為空
```

模型是否隨 repository 或 release 提供，以 [ASSETS.md](ASSETS.md) 的 provenance 與核准雜湊為準；一般貢獻者不需要模型也能修改及測試純核心邏輯。`hmm_pos.bin` 預設為 LXA3 i16 定點量化格式，runtime 仍可讀取既有 LXA2 f32 POS 資產。

## Usage｜使用方式

### CLI

有模型後可執行：

```bash
cargo build --release -p lingxi-cli
echo "市值縮水約1200億美元" | ./target/release/lingxi --format tsv

# --lexicon 可重複；新格式沒有 frequency
echo "搭板南線後感到欣慰" | ./target/release/lingxi \
  --lexicon resources/examples/transit-tw.json \
  --format annotated-json

# 文件分析模式互斥；摘要／短語／斷句／子句輸出 JSONL。
# 摘要數字是 paragraph／blockquote 的最大 block 數；code、list、table、HTML 不計入。
# 長段落與長清單項才進行 block 內 clause 摘要。
lingxi --summary 10 --min-explainability 0.35 article.txt
lingxi --keyphrases 10 --stopwords stopwords.txt article.txt
lingxi --sentences article.txt
lingxi --clauses article.txt
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

# 自訂辭典不含詞頻；lexicons 可同時載入多份 JSON 路徑或 dict。
seg = lingxi.load(
    lexicons=[
        {
            "schemaVersion": 1,
            "id": "transit-tw",
            "domain": "transportation",
            "priority": 2,
            "enabled": True,
            "entries": [{"word": "板南線", "pos": "Nc"}],
        }
    ]
)
annotations = seg.annotate("搭板南線後感到欣慰")

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

# 斷句、結構感知子句、抽取式摘要與相鄰關鍵短語都保留原文位置。
seg.split_sentences("第一句。第二句！")
seg.split_clauses("結論（含條件，不拆開），但不得省略。")
seg.extract_summary(
    "要摘要的多句長文本",
    max_blocks=10,  # 可排名的 paragraph／blockquote 上限
    similarity="bm25",
    min_explainability=0.35,
)
seg.extract_keyphrases("要分析的長文本", top_k=10, min_occurrences=1)

```

### WASM 與 C ABI

WASM binding 位於 `crates/lingxi-wasm`，模型由 JavaScript 載入後傳入：

```bash
cd crates/lingxi-wasm
wasm-pack build --release --target web
```

既有 LXA2 POS 資產可離線量化為 LXA3：

```bash
cargo run -p lingxi-convert -- --quantize-pos assets/hmm_pos.lxa2.bin assets/hmm_pos.bin
```

C ABI 位於 `crates/lingxi-ffi`，公開標頭為 `crates/lingxi-ffi/include/lingxi.h`：

```bash
cargo build --release -p lingxi-ffi
```

## 新資產與相容介面

`resources/affect/emotion-taxonomy.json` 與 `emotion-lexicon.json` 是可人工審閱的來源；`lingxi-convert` 會把它們轉成可選的 `assets/affect.bin`。`Dict.json`、`dict.bin` 與詞頻批次不承擔情感資料。

Rust 可用 `SegmenterOptions { custom_lexicons }` 搭配 `from_asset_dir_with_options`／`from_models_with_options`，再以 `annotate()` 取得詞界、POS、情感與自訂辭典來源。舊的 `from_asset_dir`、`from_asset_dir_with_user_dict`、`tokenize` 與 `Token` 保持不變。WASM 提供 `Segmenter.fromAssets(...)`，C ABI 提供 `lingxi_new_from_dir_v2`、`lingxi_annotate_json` 與 `lingxi_utf8_free`；舊 constructor／ABI 仍保留。

格式、taxonomy、遷移與授權說明見 [resources/affect/README.md](resources/affect/README.md)。

## 演算法管線

```text
文字
  → 正規化（ASCII 小寫 + 異體字）
  → 確定性保護（URL / Email / 英數 / 數量 / 年份 / 時間 / 標點）
  → Han 塊建立所有 BMES 詞段、主詞典與結構化自訂詞命中索引
  → 單一全域二階 Viterbi（主詞典使用 log(freq/total)；自訂詞使用 BMES + 6.0 + priority×0.5）
  → 固定詞界的全句二階 POS Viterbi
     ├─ 已知詞：完整 P(tag|word)
     └─ OOV：字元 joint-state POS HMM
  → Token（原文區間 + 詞性）
  → annotate 可選查詢獨立 affect 索引與自訂辭典來源
  → 文件分析：斷句／TextRank 關鍵字與短語／抽取式摘要
```

## Repository 結構

| 路徑 | 用途 | 公開狀態 |
|---|---|---|
| `crates/lingxi-core` | 分詞、HMM、POS、自訂詞典、TextRank 關鍵字／摘要 | 可公開 |
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

以下結果使用 Trident 0.7.12 `load_examples_data("chinese")` 的完整測試集：4,263 句、265,287 字，分詞 BMES gold 共 163,498 詞。測試於 2026-08-23 在 Windows x86_64（24 logical processors）、Python 3.10.10 與 rustc 1.96.0 上執行；LingXi 使用 0.3.0 release CLI 與本機 LXA2 模型，jieba 版本為 0.42.1。

| 模型 | 載入時間 | 純分詞時間中位數 | 句／秒 | Word P | Word R | Word F1 | Boundary F1 | 整句完全一致 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| jieba 0.42.1 | **338.42 ms** | 690.85 ms | 6,170.6 | 75.75% | 73.80% | 74.76% | 89.41% | 2.49% |
| LingXi 0.3.0 | 349.00 ms | **344.00 ms** | **12,392.4** | **88.57%** | **85.12%** | **86.81%** | **94.81%** | **12.69%** |

分詞正確性以 Trident BMES 詞界計算 micro Word precision、recall 與 F1；Boundary F1 比較相鄰字元間的詞界，整句完全一致則要求該句所有詞界皆符合 gold。LingXi 的純分詞速度約為 jieba 的 2.01 倍，Word F1 高 12.05 個百分點；若連同各自模型載入時間計算，端到端時間分別約為 693.90 ms 與 1,029.27 ms，LingXi 約快 1.48 倍。

兩者皆先暖機，再完整執行 3 次並取處理時間中位數；Trident 資料集載入時間與模型載入時間不計入純分詞時間。LingXi 數字來自 release CLI 的 `words` 模式（含輸出序列化），jieba 則在同一 Python 行程內逐句執行 `cut(cut_all=False, HMM=True)`。不同硬體、模型資產與執行環境的時間不可直接互比。

內部維護者備妥 `assets/` 與 0.2.2 對照包後，可重現本次報告：

```bash
python corpus/compare_segmentation_trident.py \
  --repeats 3 \
  --output-json .corpus-work/model-evaluation/trident-full-segmentation.json \
  --output-markdown .corpus-work/model-evaluation/trident-full-segmentation.md
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
