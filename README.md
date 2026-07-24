# lingxi-rs

繁體中文（台灣語料）分詞＋詞性標註引擎。舊版 C#/.NET Framework LingXi 的 Rust 重寫：
單一核心、跨平台、無 UI，交付 CLI / Python / WASM-JS / C ABI 四種形態。

## 演算法管線

```
文字 → 正規化(ASCII小寫+異體字) → 預切塊(URL/email/英數/數字/時間/標點)
     → Han 塊: daachorse AC 一次掃描建 DAG → 由右至左 DP 最佳路徑
     → 連續單字 run: 二階 BMES HMM Viterbi 合併未登入詞
     → OOV 詞: joint-state(BMES×詞性) POS Viterbi；詞典詞詞性直接查表
     → Token { byte 區間, 詞性 }（零拷貝，詞由呼叫端切片）
```

附加功能（建立在上述管線之上）：
- **自訂詞典**：建構時載入 jieba 格式詞條（`詞 [頻率] [詞性]`），建成第二個
  AC 自動機與主詞典共同建 DAG 邊。頻率省略時自動推定為「恰好贏過現行切分」
  （jieba `suggest_freq` 語意——只保證贏過詞內部切分；若被跨界詞搶走，改給
  顯式高頻率）。同詞覆蓋主詞典時取機率高者，詞性一併覆蓋。
- **TextRank 關鍵字抽取**：`extract_keywords(text, top_k)`，jieba 相容參數
  （window=5、d=0.85、10 次迭代），預設候選詞性為名詞類/動詞/英文詞，
  可用 `allow_tags` 白名單覆寫。純演算法、無額外模型資產。

與舊版的主要差異（刻意簡化，黃金集驗證等價或更好）：
- 移除 MM/RMM 雙向多候選評分：全 DAG 全域 DP 是其嚴格超集
- 二階 HMM 改為數學正確的 16 複合狀態標準 Viterbi（舊版為含硬編碼特例的樹狀近似）
- 移除 word_state_tag（16.5MB）：只有 OOV 詞才需要 POS Viterbi
- 排除詞典噪音：unknownnew 條目（7,789 筆）與 TaiwanDict n-gram（全 freq 0）
  ——這些假邊會搶走正詞（如「與國」搶「國民黨」）

## Workspace

| crate | 內容 |
|---|---|
| `crates/lingxi-core` | 全部演算法；`Segmenter::cut / tokenize / cut_segments` |
| `crates/lingxi-cli` | `lingxi` 執行檔：stdin/檔案 → words/tsv/jsonl |
| `crates/lingxi-py` | PyO3 + maturin，wheel 內附模型，`cut_batch` rayon 平行釋放 GIL |
| `crates/lingxi-wasm` | wasm-bindgen；模型由 JS fetch 傳入；offset 為 UTF-16 |
| `crates/lingxi-ffi` | C ABI（.dll/.so/.a）＋手寫 `include/lingxi.h`；token 零拷貝 |
| `tools/lingxi-convert` | 一次性：舊版 JSON 模型 → `assets/*.bin`（postcard + xxh3 校驗） |

## 建置與模型轉換

```bash
# 1. 從舊版 JSON 轉出二進位模型（assets/*.bin 不進 git）
cargo run --release -p lingxi-convert -- <Resources目錄> <ModelingData目錄> assets

# 2. 測試（含黃金集與真實語料驗證；資產不存在時自動跳過）
cargo test --release

# 3. CLI
echo "金管會前主委參加記者會" | ./target/release/lingxi --format tsv

# 4. Python wheel（自動搬資產＋建置；--convert 可在 assets 缺少時代跑轉換）
python tools/build_wheel.py

# 5. WASM
cd crates/lingxi-wasm && wasm-pack build --release --target nodejs
```

## Python 用法

```python
import lingxi
seg = lingxi.load()                    # wheel 內附模型；或 load(asset_dir=...)
seg.cut("金管會前主委參加記者會")        # -> list[str]
seg.tokenize("...")                    # -> list[Token(word, tag, start, end)]，字元座標
seg.cut_batch(texts)                   # rayon 平行，釋放 GIL

# 自訂詞典：檔案路徑或詞條行列表（jieba 格式）
seg = lingxi.load(user_dict=["板南線 nt", "柯文哲 nr", "鹽酥雞 100000 n"])

# TextRank 關鍵字 -> [(詞, 權重)]，權重降冪
seg.extract_keywords(text, top_k=20)
seg.extract_keywords(text, top_k=20, allow_tags=["n", "nt", "ns"])
```

CLI 對應：`--user-dict 詞典檔`、`--keywords N`（全文抽取模式）。

## 實測數據（Windows 11, x86_64）

- 吞吐：單執行緒 30–37 MiB/s（目標 2 MB/s 的 15 倍）
- 載入：45 ms（21.7MB dict.bin + 兩個 HMM 資產）
- Python 批次：10,000 句 / 16 ms
- WASM：wasm-opt 後 1.02MB，gzip 361KB（不含詞典）
- must-pass 黃金集：90/90；語料 500 行覆蓋不變量全過

## 已知限制

- 詞典頻率噪音：部分條目頻率來自舊 PTT 語料的錯誤切分（如「民黨」freq 11 萬），
  長期解法是用乾淨語料重訓頻率
- 人名辨識依賴 HMM/POS（舊版姓氏表規則已移除）：「柯文哲」若姓氏+名首字
  恰為詞典詞（柯文）會切錯——可用自訂詞典（`柯文哲 nr`）修正
- 自訂詞典的自動頻率只保證贏過詞內部切分；被跨界詞搶走時（如「吃鹽」搶走
  「鹽酥雞」的首字）需給顯式高頻率
- 全形數字（０-９）未特別處理（與舊版行為一致）
