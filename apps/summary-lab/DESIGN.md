# LingXi Summary Lab — 設計與實作規格

Written against: working tree（摘要與子句功能尚未提交）

## Evidence chain

- Surface：`apps/summary-lab/` 本機 Web 工作台
- Problem：需要排除 LLM 自行改寫造成的摘要偏誤，並能逐句檢查 deterministic 計算依據
- Design evidence：LingXi core 的子句切分、TextRank、`explainability` 與 `SummarySignals`
- Owner：`apps/summary-lab/`、`crates/lingxi-cli/src/main.rs`
- Scope：單次貼文、摘要結果、token 比較、逐句診斷、累積曲線
- Uncertainty：token 數依 `o200k_base` 估算，並非所有模型共用的唯一 token 數

## Context brief

- Audience：NLP／prompt／摘要演算法維護者與測試人員
- System purpose：以完全不呼叫 LLM 的流程測試抽取式摘要
- Primary job：貼上文字並確認 LingXi 選出了哪些原文子句、為什麼
- Primary task on screen：填入內容後按「執行」取得可稽核摘要
- Entry focus：大面積文字輸入與唯一 primary CTA
- Navigation mechanism：結果出現後，以「摘要結果／句子診斷」tabs 切換平行視圖
- Next-step handoff：先看 before/after，再按需進入逐句診斷
- Brand / tone：紙本校樣與測量儀器；冷靜、可信、可稽核
- Emotional job：讓使用者確信沒有隱藏生成步驟
- Avoided tone：AI 聊天介面、行銷 hero、分析卡片牆
- Voice register：直接、技術但可讀
- Official brand source：無另行使用外部品牌；沿用 repository 既有「紙與墨」產品語言
- Constraints：本機限定、零外部 API、responsive、WCAG AA、不可將輸入外送
- Memorable hook：原文與摘要像校樣紙並排；特殊加權訊號如校稿色標直接落在句子旁
- Open assumptions：預設最多 12 個子句、可解釋性門檻 0.35

## User story and flow

```text
As a 摘要演算法測試者
I enter this screen when 我需要驗證一段文字的抽取結果
I need to 貼上原文並執行 deterministic 摘要
So that 我能比較 token 數並逐句稽核選取理由
After that I should be guided to 句子診斷與累積曲線
```

```text
貼上內容 → 執行 → Rust core 分析 → 摘要結果 → 句子診斷（按需）
```

## Task model

| Level | Goal |
|---|---|
| Primary goal | 貼入原文並執行 deterministic 摘要 |
| Secondary goal | 比較 before / after token 數與計算耗時 |
| Low-frequency goal | 調整一般候選軟上限與可解釋性門檻 |
| Rare goal | 逐句稽核特殊加權、分數與累積曲線，診斷空結果或模型資產錯誤 |

## State model

| State | Entry condition | Must show | Hidden / deferred | Primary CTA | Transition |
|---|---|---|---|---|---|
| `empty` | 尚未輸入 | textarea、簡短零 LLM 說明 | 所有結果 | 執行（disabled） | drafting |
| `drafting` | 有輸入 | textarea、字數、執行 | 結果 | 執行 | running |
| `running` | 已送至本機 CLI | textarea、處理狀態 | 舊結果操作 | 計算中 | resolved / error |
| `resolved` | 回傳有效 report | tabs、before/after、度量 | 非當前 tab | 執行 | running |
| `error` | CLI／資產／輸入錯誤 | 原因與修法、原輸入 | 結果 | 重新執行 | running |

## Information architecture

| Item | Role | Frequency | First viewport | Visibility | Container |
|---|---|---:|---:|---|---|
| 文字輸入與執行 | action-critical | 高 | 是 | always | primary stage |
| 字數與本機／零 LLM 狀態 | status-feedback | 高 | 是 | always | inline toolbar |
| threshold / max clauses | reference | 低 | 否 | details open | disclosure |
| token 與耗時比較 | decision-supporting | 高 | 結果後 | resolved | metrics strip |
| before / after | decision-supporting | 高 | 結果後 | summary tab | compare stage |
| 逐句分數與訊號 | audit | 中 | 否 | diagnostics tab | diagnostic list |
| 累積曲線 | audit | 中 | 否 | diagnostics tab | figure |
| 錯誤修復 | exception-handling | 低 | 條件式 | error | inline alert |

Primary question：這段原文經 LingXi deterministic 計算後，最佳摘要是什麼？

- kept in first viewport：textarea、執行、字數、零 LLM 狀態
- deferred：進階門檻、逐句表、曲線、方法說明
- tabs 必須實際控制對應 `tabpanel[hidden]`

## Content audit

- `must-see-now`：文字輸入、字數、本機／零 LLM 狀態、執行。
- `next-step-only`：token 比較、耗時、before / after、結果 tabs。
- `error-only`：CLI 未建置、模型不存在、輸入或 report 無效。
- `on-demand-reference`：一般候選軟上限、門檻、所有逐句分數、累積曲線。
- `keep-off-first-viewport`：完整句子診斷、訊號圖例、演算法細節。

## Deferred blocks and reveal rationale

| id | hidden_now_because | reveal_trigger | container |
|---|---|---|---|
| `advanced-settings` | 預設值足以開始主任務，常駐會分散注意 | 開啟「進階設定」 | native details popover |
| `results` | 尚未計算時沒有可信內容 | report 成功回傳 | results stage |
| `diagnostics` | 初步判讀只需摘要與 before/after | 切換「句子診斷」 | tabpanel |
| `error-message` | 正常狀態不應佔用主舞台 | API／CLI error | inline alert |

## Design decision

採單一工作台，不設 hero 或 KPI 卡牆。輸入面佔據首屏主舞台；解析後才揭露一條 metrics strip 與兩個結果 tabs。摘要 tab 是校樣式 before/after，診斷 tab 是曲線加可掃描逐句列表。

## Canonical token board

完整 canonical 值與實作映射見 `docs/design-token-board.md`。

- Theme：`Proof Desk / 校樣桌`
- Audience fit：高信任、需要追溯計算理由的技術工作
- Dominant direction：暖紙面、深墨字、細線刻度、低彩度校稿標籤
- Palette：
  - canvas `#ece7dc`
  - paper `#fbf8f0`
  - ink `#18313c`
  - muted `#66747a`
  - line `#c9c1b4`
  - primary `#176b63`
  - danger `#a23f49`
  - proper noun `#2f6f9f`
  - negation `#ad3e47`
  - emphasis `#9b6b13`
  - list `#33745a`
  - object `#74549a`
  - date `#a4542f`
  - number `#466d91`
  - quantity `#8b4d72`
  - acronym `#4f5f99`
- Typography：標題 `Noto Serif TC / Songti TC / PMingLiU`；介面 `Noto Sans TC / Microsoft JhengHei`；數值 `IBM Plex Mono / Consolas`
- Spacing：4、8、12、16、24、32、48
- Radius：4、8、12；避免大量 pill 與卡片感
- Shadow：只有 sticky action 與浮層使用一層柔影；主面板以邊框區隔
- Motion：120ms fast、180ms base；只用於 focus、tab、結果 reveal；遵守 reduced motion
- Component tone：按鈕像明確的測量啟動鍵；輸入像校樣紙；狀態像儀器讀值；標籤文字與顏色並用
- Do：讓原文、摘要、分數與訊號可掃描；使用真實標籤補足色彩語意
- Don't：聊天泡泡、漸層文字、霓虹、玻璃擬態、summary card farm

## Implementation slices

1. 主舞台與 deterministic report API
   - Files：CLI report、server、input shell
   - Verify：可輸入並回傳 JSON；沒有任何外部請求或 LLM dependency
2. 結果 tabs 與 before/after
   - Files：`app.js`、`styles.css`
   - Verify：token、耗時、摘要；tab 真正切換 panel
3. 診斷曲線、訊號色標與 QA
   - Files：diagnostic rendering、tests、README
   - Verify：mobile/desktop、keyboard、empty/loading/error、screenshot critique

## Reusable component guideline

### Usage

`SignalTag` 用於指出規則引擎實際命中的特殊加權訊號；沒有命中時不顯示，也不拿來裝飾一般狀態。

### Layout

標籤靠近對應子句，允許換行但不可遮住正文。表格窄螢幕時維持文字欄優先，分數欄保留可比較寬度。

### Anatomy

每個標籤包含色塊、短標籤與可讀名稱；色塊只是輔助，文字必須獨立傳達語意。

### States & Spec

預設為白底、訊號色邊框與文字；選取子句可加淡色背景，但不得只靠背景色表達「已選入」。最小高度 1.55rem。

### Interaction

標籤不具互動性、不接收焦點。若未來增加篩選，應以獨立按鈕提供並維持 `aria-pressed` 狀態。

### Content / Asset

固定用語為「專有名詞、否定詞、強調、條列、物件名、日期、數字、數值＋單位、縮略語」；圖例與列內標籤共用同一套名稱與顏色 token，不使用外部圖示資產。

## Validation and stop conditions

- Product：貼入含否定、粗體、條列、函數名的文本，摘要只含原文片段
- Interface：390×844、1024×768、1440×900；keyboard-only；長句與空結果
- System：report 由 `lingxi-core` 產生，Web 不重寫摘要演算法
- Repository：`cargo test/check`、`npm test`、frontend audits
- Stop if：CLI 無法載入本機模型、報告需要外部 API、或任何輸入被傳送到 localhost 以外
