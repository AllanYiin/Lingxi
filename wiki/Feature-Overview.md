# LingXi 功能介紹

本頁說明 LingXi `0.3.0` 提供的能力、適合使用的情境及產品界線。若要理解每一步如何運作，請接著閱讀[核心原理](How-LingXi-Works)。

## 1. 繁體中文分詞

LingXi 將沒有空格的中文句子切成可供搜尋、統計、標註或下游模型使用的詞。它不是單純採最長詞匹配，而是把可能的詞段放進同一個全域解碼問題，再由二階 BMES Viterbi 選出整句分數最高的詞界組合。

主要特性：

- 以繁體中文與台灣語料為主要使用情境。
- 主詞典與字元 BMES 模型共同參與全句決策。
- 多字詞典命中可提供詞級機率證據；單字仍完全由 BMES 模型決定。
- 未登入詞仍可由字元模型組成，不要求所有詞都事先存在於詞典。
- 回傳原文 UTF-8 byte 區間，方便上層介面避免複製並保留來源位置。

適合用於搜尋前處理、文本索引、語料分析、文字標註與其他需要穩定詞界的繁體中文 NLP 流程。

## 2. 結構化內容與規則保護

URL、Email、英數、時間、百分比、數量級與標點不適合直接交給一般中文分詞模型。LingXi 會先辨識並保護這些範圍，再讓其餘 Han 區段進入統計解碼。

這項設計有兩個目的：

- 避免網址、信箱或數字格式被逐字切碎。
- 把確定性高的格式交給規則，把語意歧義留給統計模型。

規則採集中註冊及 trace 設計，可透過 `cut_with_trace` 或 `tokenize_with_trace` 檢視實際觸發的規則，方便回歸測試與問題診斷。

## 3. 詞性標註

`tokenize` 會在分詞完成後，為每個詞選擇 CKIP 原生詞性代碼。整句使用二階 POS Viterbi，因此目前詞性的選擇可參考前兩個狀態，而不是逐詞獨立判斷。

- 已知詞使用完整的 `P(tag|word)` 詞彙分布。
- 未登入詞使用字元層級的 `BMES × POS` joint-state HMM。
- URL、Email、數字、時間與標點使用確定性內建詞性。
- POS 階段不能移動既有詞界，輸出較容易理解與除錯。

## 4. 多領域自訂辭典

結構化自訂辭典使用無詞頻 JSON 格式，可同時載入多份資料，並記錄：

- `schemaVersion`：目前為 `1`。
- `id`：辭典的穩定識別碼。
- `domain`：如交通、醫療或金融等領域。
- `priority`：`-10` 到 `10`，用來調整多份辭典衝突時的偏好。
- `enabled`：是否載入該份辭典。
- `entries`：詞形與可選的詞性、情感資料。

自訂辭典不修改主詞典的總詞頻，因此不會因為加入領域詞而重新正規化整個基礎詞典。`annotate` 也能回報命中的辭典 `id`、`domain` 與 `priority`，讓下游知道結果來源。

範例：

```json
{
  "schemaVersion": 1,
  "id": "transit-tw",
  "domain": "transportation",
  "priority": 2,
  "enabled": true,
  "entries": [
    { "word": "板南線", "pos": "Nc" }
  ]
}
```

## 5. 詞級情感與語意標註

選擇性 `affect.bin` 資產可讓 `annotate` 在既有 token 上附加：

- polarity（正向、負向、中性、混合或依上下文而定）。
- 一個或多個 emotion labels。
- `contextDependent`、`semanticFlags`、`appraisals`、備註與來源。

情感資料與主詞頻模型分離；缺少 `affect.bin` 時仍可正常分詞與標註詞性，只是 `affect` 欄位為空。

> 這是詞表查詢式的詞級提示，不處理句級分類、否定作用域、反諷、上下文消歧或情緒強度。它不應被描述為完整的情緒理解模型。

## 6. TextRank 關鍵字抽取

LingXi 先進行分詞與詞性標註，再篩選候選詞，使用寬度為 5 的滑動視窗建立無向加權共現圖，接著以阻尼係數 `0.85` 執行 10 次 PageRank 型迭代並回傳前 `top_k` 個詞。

預設另有專有名詞通道：對 CKIP `Nb` 詞依詞頻與首次出現位置給予有界軟加分，並提供可調整的融合權重與占比上限。這項加分不保證任何專有名詞一定入榜。

## 7. 共用核心、多種整合介面

| 介面 | 適用情境 | 主要入口 |
|---|---|---|
| Rust core | 直接嵌入 Rust 應用 | `crates/lingxi-core` |
| CLI | shell pipeline、批次處理與快速驗證 | `lingxi` |
| Python | 資料分析、服務端與 NLP workflow | `lingxi.load()` |
| WASM/JavaScript | 瀏覽器或 JavaScript runtime | `Segmenter.fromAssets(...)` |
| C ABI | C/C++ 或其他可呼叫 C ABI 的語言 | `lingxi_new_from_dir_v2` 等函式 |

所有 binding 共用 `lingxi-core`，因此詞界、詞性和主要演算法行為不需要在不同語言重做一套。I/O、模型載入方式與批次平行化則由各上層介面負責。

## 已知限制與部署界線

- 完整執行至少需要 `dict.bin`、`hmm_bmes.bin`、`hmm_pos.bin`；目前不隨原始碼 repository 發布。
- `affect.bin` 是可選資產，缺少時不影響分詞、詞性與關鍵字功能。
- 模型與語料授權和 MIT 原始碼授權分開管理，不可因程式碼可公開就推定模型可再散布。
- 關鍵字權重是文件內的相對排序訊號，不是跨文件可直接比較的絕對機率。
- 效能與正確性會受模型版本、硬體、編譯器及輸入分布影響；最新測試條件請以[專案 README](https://github.com/AllanYiin/Lingxi#trident-%E5%85%A8%E9%87%8F%E6%B8%AC%E8%A9%A6lingxi-vs-jieba)為準。

## 下一步

- 想理解各能力如何串接：閱讀[核心原理](How-LingXi-Works)。
- 想直接執行：前往[專案 README](https://github.com/AllanYiin/Lingxi#usage%E4%BD%BF%E7%94%A8%E6%96%B9%E5%BC%8F)。
- 想更新這套 Wiki：閱讀[發布至 GitHub Wiki](Publishing-the-Wiki)。
