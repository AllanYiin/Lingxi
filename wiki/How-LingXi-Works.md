# LingXi 核心原理

LingXi 的核心設計是：先把高確定性的格式保護起來，再把中文詞界當成全句最佳化問題，最後在固定詞界上處理詞性與其他標註。這樣能讓每個階段的責任清楚，也避免後段資訊偷偷改變前段結果。

## Overview｜整體資料流

```text
原始文字
  │
  ├─ 1. 正規化：ASCII 小寫、異體字統一
  │
  ├─ 2. 混合詞保護：保留跨 ASCII / Han 邊界的詞典詞
  │
  ├─ 3. 確定性預切：URL、Email、英數、數量、時間、標點
  │
  ├─ 4. Han 區段候選：字元 BMES 路徑、主詞典與自訂辭典命中
  │
  ├─ 5. 全域二階 BMES Viterbi：選出最佳詞界
  │
  ├─ 6. 保守的 post hooks：在 POS 前完成最終詞界
  │
  ├─ 7. 固定詞界的全句二階 POS Viterbi
  │
  ├─ 8. annotate：選擇性查詢詞級情感與自訂辭典來源
  │
  ├─ 9. extract_keywords：由 token 建圖並執行 TextRank
  ├─ 10. extract_keyphrases：重組相鄰高排名關鍵詞
  └─ 11. extract_summary：結構感知子句抽取、建立子句圖、可解釋性門檻與去冗餘
```

## 1. 正規化與原文位置

輸入先依模型字典做正規化，例如將 ASCII 英文字母轉成小寫並統一已知異體字。這讓 `AI` 與 `ai` 等表面差異能對應到一致的模型節點。

分詞結果使用原文 UTF-8 byte 起訖位置，而不是只回傳複製後的字串。上層可以用區間取回原文，也能將標註對齊到其他資料結構。

## 2. 為什麼先保護結構化文字

網址、Email、數字與時間具有明確格式。如果把 `https://...` 或 `1200億` 直接送進一般中文模型，模型必須處理大量本來可由規則確定的狀況，也更容易產生破碎詞段。

LingXi 因此先蒐集受保護範圍，依左到右順序切成不重疊區段。一般英數串和標點也會直接分類；只有 Han 區段進入後續詞界解碼。中文數字則刻意保持保守，例如「一起」不會只因含「一」就被數字規則搶走。

規則結果可產生 trace，記錄 rule id、前處理或後處理階段、動作與 byte 區間。這使規則變更可以被測試，也能回答「這段文字為什麼被保護」。

## 3. BMES 如何表示詞界

每個中文字元會處在下列四種狀態之一：

| 狀態 | 意義 | 範例「台北市」 |
|---|---|---|
| B | 多字詞開頭（Begin） | 台 |
| M | 多字詞中間（Middle） | 北 |
| E | 多字詞結尾（End） | 市 |
| S | 單字成詞（Single） | 單獨一字時使用 |

合法詞段的形狀因此是 `S`、`BE` 或 `BM...E`。模型為字元發射分數、句首分數、一階轉移與二階轉移提供統計證據。

「二階」表示目前狀態可以參考前兩個狀態。相較只看前一個狀態，它能表達更多局部上下文，但仍可用動態規劃有效求解。

## 4. 詞典不是硬切，而是全域路徑證據

對每個 Han 區段，LingXi 建立所有可能詞段。每條候選路徑的概念分數可理解為：

```text
路徑分數
  = 句首／跨詞轉移分數
  + 字元 BMES 證據
  + 詞典或自訂辭典提供的詞級證據
```

實際規則如下：

- 沒有詞典命中的候選，使用完整 BMES 分數。
- 主詞典的多字詞命中，以 `ln(freq / total)` 取代詞內 BMES 分數，但仍保留句首與跨詞轉移。
- 結構化自訂詞保留 BMES 分數，再加上 `6.0 + priority × 0.5` 的有界偏好。
- 單字候選不使用詞典捷徑，完全由 BMES 決定。

Viterbi 動態規劃會保留每個位置與二階上下文下分數最高的路徑，走完整個區段後再沿 backpointer 還原詞界。因為所有候選在同一個全域問題中競爭，詞典命中不是不看上下文的強制最長匹配。

## 5. 自訂辭典如何保持可控

自訂辭典的 `priority` 限制在 `-10..=10`，而且不改寫主詞典的總頻率。這帶來三個維護特性：

- 加入領域詞時，不會讓所有既有詞的主詞典機率一起漂移。
- 多份辭典可用穩定 `id` 與 `domain` 管理，輸出也能追蹤命中來源。
- 優先序只提供可預期的偏好，不會把自訂詞改造成無條件硬切。

若不同辭典在正規化後產生相同詞形，較高 `priority` 的項目會勝出；同優先序時則由後載入項目覆蓋。部署時應固定載入順序並用 golden tests 保護重要詞界。

## 6. 詞性為什麼要在固定詞界後執行

LingXi 先完成分詞，再對連續、非確定性詞性的詞段執行全句 POS Viterbi。對每個詞：

- 若詞存在 POS 詞彙模型，候選為完整 `P(tag|word)` 分布。
- 若是未登入詞，模型以每個候選 tag 對應的 `BMES × POS` 字元狀態計分。
- 若 runtime 辭典提供明確詞性，可作為 lexical fallback。
- URL、Email、數字、時間、標點等類型直接使用內建詞性。

POS 模型仍保留句首、一階與二階跨詞轉移，所以能利用整句上下文；但它收到的是已固定的詞陣列，無法改變任何 byte 邊界。這個單向資料流讓分詞錯誤與詞性錯誤可以分開診斷。

## 7. 詞級情感是查詢，不是句級推論

`annotate` 會沿用分詞與 POS 結果，再以正規化後的完整詞形查詢獨立 `AffectStore`。若命中，就把 polarity、emotion labels、context flag、semantic flags、appraisals、備註與來源附加到 token。

這個階段不重切詞、不改詞性，也不分析否定、反諷或句子上下文。其優點是行為可追溯且資產可獨立維護；限制是不能把詞級命中直接解讀為整句情緒。

## 8. TextRank 如何產生關鍵字

關鍵字抽取建立在既有 token 之上：

1. 先依詞性與詞長篩出候選；預設涵蓋 CKIP 名詞、動詞與外文詞。
2. 在 5-token 視窗內為共同出現的候選詞建立無向加權邊。
3. 以阻尼係數 `0.85` 進行 10 次就地 PageRank 更新。
4. 將分數正規化，再選擇性融合專有名詞通道。
5. 依權重降冪輸出 `top_k`；同分時以詞形排序，確保結果穩定。

專有名詞通道對 `Nb` 詞的詞頻與首次出現位置分別計分，再以飽和式公式提供不超過 1 的軟加分。它能提升實體名稱的可見度，但不保證入榜，也不會取代共現圖分數。

## 9. 關鍵短語與抽取式摘要

`extract_keyphrases` 先使用可配置 TextRank 取得候選關鍵詞，再依原文 token 相鄰關係組成短語，聚合同一正規化短語的出現次數與原文 byte spans。這是統計式關鍵短語，不等同完整的術語或實體辨識。

`extract_summary` 必定先抽取子句。逗號、分號與冒號可形成子句界，但括號、引號、反引號、Markdown `**粗體**`、數字千分位與尚待後果的條件前件受到保護；換行條列則各自保留句域。接著以 LingXi 分詞及 CKIP 詞性建立內容詞向量，子句圖可使用對稱 BM25（預設）或詞頻 cosine，相似度作為邊權重並執行 TextRank。

每個候選另標記九類可直接說明的訊號：`Nb`／`Nc` 專有名詞、否定詞、強調格式、條列項目、反引號／函數呼叫／dotted name 等函數或工具物件名、日期、數字、帶單位數值與全大寫縮略語。`不只`／`不僅` 是關聯結構，不計為否定；縮略語若同時位於括號或引號等強調結構中，視為定義性內容並硬保留。候選可解釋性分數為：

```text
explainability
  = 0.45 × TextRank relevance
  + 0.20 × marginal coverage gain
  + 0.10 × lexical novelty
  + 0.25 × recognized-signal coverage
```

`top_k` 只限制一般候選的最大數量；低於 `min_explainability`（預設 `0.35`）的一般候選不納入，也不為湊滿數量而回填。條列、日期、數字、帶單位數值，以及括號或引號中的全大寫縮略語是硬保留事實，可略過門檻並使結果超過 `top_k`。這是絕對且可檢查的可解釋性門檻，不是固定 15% 的相對邊際效應。若 Markdown 標題與條列已占主要內容，則完整保留整份結構化筆記。選取以詞彙及有限規則型語意重疊避免高度重複，最後預設依原文順序輸出。每個結果均保留原文、byte offset、句序、子句序、權重、各分項與訊號；它是抽取式摘要，不會改寫或生成句子。

## 10. 一個核心如何服務多個介面

`lingxi-core` 負責模型載入、規則、分詞、POS、情感查詢與 TextRank；CLI、Python、WASM 與 C ABI 僅處理各自環境的資料轉換、I/O、記憶體生命週期或批次平行化。

這種切分降低跨語言行為分歧：核心演算法修正一次，各 binding 即可共用。相對地，模型如何取得、資產能否發布及上層錯誤呈現仍由部署環境負責。

## Design decisions and trade-offs｜設計決策與取捨

| 設計決策 | 主要收益 | 取捨 |
|---|---|---|
| 規則先保護高確定性格式 | URL、Email 與數量格式較穩定 | 新格式需明確擴充規則與回歸測試 |
| 全域二階 BMES 解碼 | 詞典與上下文可在同一路徑競爭 | 模型與動態規劃比單純最長匹配複雜 |
| POS 不回頭修改詞界 | 錯誤來源容易分離與重現 | 放棄分詞與 POS 聯合解碼可能得到的全域最優解 |
| 多介面共用 Rust 核心 | 跨語言結果一致、修正集中 | binding 仍需各自處理 I/O 與記憶體生命週期 |

## 原始碼對照

- [主管線與 Segmenter](https://github.com/AllanYiin/Lingxi/blob/master/crates/lingxi-core/src/lib.rs)
- [BMES 與詞典全域解碼](https://github.com/AllanYiin/Lingxi/blob/master/crates/lingxi-core/src/dag.rs)
- [固定詞界 POS Viterbi](https://github.com/AllanYiin/Lingxi/blob/master/crates/lingxi-core/src/pos.rs)
- [預切塊](https://github.com/AllanYiin/Lingxi/blob/master/crates/lingxi-core/src/chunk.rs)
- [規則 registry 與 trace](https://github.com/AllanYiin/Lingxi/blob/master/crates/lingxi-core/src/rules.rs)
- [自訂辭典 schema](https://github.com/AllanYiin/Lingxi/blob/master/crates/lingxi-core/src/custom_lexicon.rs)
- [詞級情感資料](https://github.com/AllanYiin/Lingxi/blob/master/crates/lingxi-core/src/affect.rs)
- [TextRank 關鍵字抽取](https://github.com/AllanYiin/Lingxi/blob/master/crates/lingxi-core/src/keyword.rs)
