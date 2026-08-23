# Design Token Board — Summary Lab

- Status：canonical
- Owner：`apps/summary-lab/styles.css`
- Related implementation：`../styles.css`
- Theme name：Proof Desk / 校樣桌
- Audience fit：需要逐句稽核與排除生成偏誤的 NLP 測試者
- System purpose：本機 deterministic 抽取式摘要測試
- Dominant direction：暖紙面、深墨字、細刻度與校稿色標
- Memorable hook：before / after 校樣紙與逐句規則色標
- Official brand source：不套用第三方品牌；延續 repository 既有紙與墨工作台語言

## 色彩系統

| Role | Value | Usage |
|---|---|---|
| canvas | `#ece7dc` | workspace background |
| paper | `#fbf8f0` | primary surface |
| ink | `#18313c` | primary text / structure |
| muted | `#66747a` | secondary text |
| line | `#c9c1b4` | separators |
| primary | `#176b63` | execute / selected / curve |
| danger | `#a23f49` | recoverable error |
| proper noun | `#2f6f9f` | named entity signal |
| negation | `#ad3e47` | negation signal |
| emphasis | `#9b6b13` | markdown / quotation signal |
| list | `#33745a` | list-item signal |
| object | `#74549a` | function / tool signal |
| date | `#a4542f` | date signal |
| number | `#466d91` | numeric fact signal |
| quantity | `#8b4d72` | number with unit signal |
| acronym | `#4f5f99` | uppercase acronym signal |

所有 signal 必須同時顯示文字 label，不以顏色單獨傳意。禁用紫藍霓虹、漸層文字與純黑白高刺激搭配。

## Typography

| Role | Family | Size | Weight | Line height |
|---|---|---:|---:|---:|
| Display / H1 | Noto Serif TC / Songti TC / PMingLiU | `clamp(1.55rem, …, 2.5rem)` | 700 | 1.25 |
| H2 | same serif stack | `clamp(1.35rem, …, 2rem)` | 700 | 1.3 |
| Body | Noto Sans TC / Microsoft JhengHei | `1rem` | 400 | 1.6 |
| Proof copy | serif stack | `1–1.12rem` | 400 | 1.85–1.9 |
| Metrics / labels | IBM Plex Mono / Cascadia Mono / Consolas | `0.65–2rem` | 400–700 | 1–1.4 |

Serif 用於原文與摘要，強化校樣語意；sans 用於控制；mono 只用於可比較數值與狀態。

## Spacing, radius, shadow, motion

- Spacing：4、8、12、16、24、32、48 px；comfortable density，讓長文可掃描但不浪費首屏。
- Radius：4、8、12 px；面板以細框為主，不使用大量圓角 card。
- Shadow：只用於浮出的進階設定；主舞台與結果以 border 分層。
- Motion：120ms fast、180ms base，standard easing `cubic-bezier(0.2, 0, 0, 1)`；禁止彈跳與裝飾動畫。

## Component tone samples

- Button：唯一 primary 是「執行」測量鍵；loading 有 spinner、disabled 與 `aria-busy`。
- Input：大面積紙張式 textarea，focus 以 teal ring 與雙線顯示。
- Panel：平面校樣紙、細線分隔，不做 card-in-card。
- Navigation：兩個 tabs 只切換平行結果視圖，對應真實 hidden tabpanel。
- Feedback：狀態文字說明目前步驟；錯誤同時提供原因與建置／資產修法。

## Do / Don't

- Do：保住輸入主任務、使用原文測試、顯示各分數與色標文字、明示 Markdown 完整保留模式、支援 keyboard 與 mobile reflow。
- Don't：聊天泡泡、hero、KPI 卡牆、假 tab、只靠顏色、向外部 host 送出原文。

## Implementation mapping

- CSS：`../styles.css`
- JS interaction：`../app.js`
- Semantic shell：`../index.html`
- Screenshot evidence：由 `scripts/visual-qa.mjs` 產生
