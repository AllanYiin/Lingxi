# LingXi Summary Lab 需求規格

## 目標與限制

- 核心功能：完全不使用 LLM 的抽取式摘要測試、token 前後比較、耗時、逐句分數、解釋性累積曲線與特殊加權標註。
- 一鍵交付：非程式使用者只需雙擊 `run_app.bat`；macOS/Linux 提供對應 wrapper。
- 摘要邊界：只能抽取原文子句，不生成、不改寫、不對外傳送輸入。
- 保留邊界：Markdown 條列、日期、數字與帶單位數值都會影響重要性；帶單位且非純日期的數值可略過一般可解釋性門檻，但所有一般摘要仍須遵守 `maxClauses` 硬上限。
- 結構化筆記：當 Markdown 標題與條列已占主要內容時，判定為作者已壓縮的重點筆記，完整保留原文而不再次簡化。
- 語意與切句：`不只`／`不僅`、`非常`、`是否` 不標成否定；條件／數值前件、定義句及「先…再…」處置鏈必須保留為完整語意單位。
- 縮略語：`FOMO`、`ETF`、`KOSPI` 等全大寫縮略語需有獨立診斷訊號；位於括號或引號中的縮略語視為定義性內容，可略過一般門檻，但仍遵守 `maxClauses`。
- 選句品質：候選選取必須同時考量重要性、尚未覆蓋的內容與相對已選內容的新穎性；低詞彙重疊時不得僅因原文位置較早而固定勝出。
- 啟動邊界：自動建立 `.venv`、安裝專案依賴、建置 Rust CLI、啟動本機 Node server、readiness probe 後開瀏覽器。
- 系統級工具：Python 3.10+、Node.js 20+、Rust stable/Cargo 為先決條件；缺少時以白話錯誤與官方安裝連結停止，不靜默安裝。

## 使用情境四軸

| 軸 | project_profile | 判定 |
|---|---|---|
| 使用者 | `small_team` | 少量非程式使用者共用 |
| 使用週期 | `occasional` | 需要時執行摘要測試 |
| 修改頻率 | `occasional` | 規則與 UI 偶爾調整 |
| 壞掉代價 | `multi_user_disruption` | 會中斷多人的驗證工作，但不屬關鍵生產服務 |

結論：`scene_b_shared_tool`。

## 輸入、輸出與資料保存

- 輸入：textarea 中的 UTF-8 文字、最大子句數、可解釋性門檻。
- 輸出：原文抽取摘要、tiktoken 估算、計算耗時、逐句診斷與累積曲線。
- 使用者文字不持久化；重新整理即清除，降低測試資料殘留風險。
- 啟動狀態保存於 `.runtime/*.json`；技術錯誤保存於 `logs/`。
- 不使用外部 API、mock LLM 或遠端儲存。

## Primary task

在單一主畫面貼入文字並按「執行」，取得可稽核、不可改寫原文的最佳摘要。

## Task model

| 層級 | 目標 |
|---|---|
| Primary | 輸入文字並取得摘要 |
| Secondary | 比較 token 與耗時 |
| Low-frequency | 調整摘要子句上限與可解釋性門檻 |
| Rare | 逐句診斷權重、排查模型資產或啟動錯誤 |

## State model

`empty → drafting → running → resolved`；例外為 `error`。結果只在 `resolved` 顯示，錯誤只在 `error` 顯示且原文保留。

## Information roles and visibility

| 區塊 | 角色 | 首屏 | hidden_now_because | reveal_trigger | container |
|---|---|---:|---|---|---|
| 文字輸入與執行 | `action-critical` | 是 | — | — | main stage |
| 本機／0 LLM 狀態 | `status-feedback` | 是 | — | — | header + inline |
| token／耗時 | `decision-supporting` | 否 | 執行前沒有數值 | state = resolved | summary tab |
| 逐句曲線與分數 | `audit/history` | 否 | 不影響第一次執行 | 使用者切到診斷 | diagnostics tab |
| 進階門檻 | `reference` | 否 | 大多數測試沿用預設 | 使用者展開進階設定 | disclosure |
| 錯誤詳情 | `exception-handling` | 否 | 正常流程不需要 | state = error | inline alert + logs |

## Content audit

- `must-see-now`：textarea、字數、本機／零 LLM 狀態、唯一主 CTA「執行」。
- `next-step-only`：摘要、token、耗時、句數。
- `error-only`：啟動／CLI／模型資產錯誤與 logs 指引。
- `on-demand-reference`：進階門檻、逐句診斷、累積曲線。
- `keep-off-first-viewport`：完整分數表、設計說明、啟動器技術 metadata。

## 驗收條件

1. Windows 雙擊 `run_app.bat` 可完成環境檢查、建置、啟動與瀏覽器開啟。
2. 缺 Python／Node／Cargo 或 port 衝突時不閃退，顯示下一步並留下 logs。
3. `run_app.*` 由 `scripts/project_launcher.py` 生成，不存在平行主流程。
4. `python scripts/apsm_validate.py --project . --strict` 通過。
5. 一鍵流程實際送出範例並得到 `llmCalls = 0` 的報告。
6. 不改動既有 Summary Lab 視覺方向、摘要核心公開介面或零 LLM 邊界。
7. 密集 Markdown 條列輸入的輸出與原文完全相同，報告明示「完整保留」模式。
8. 一般摘要的輸出不得超過 `maxClauses`；日期與裸數字只提高權重，不得單憑訊號排擠明示的研究結論。
9. `不只`／`不僅`／`非常`／`是否` 的否定計數為 0；「每天久坐超過10小時，…後果…」不得被截成孤立前件。
10. `Fear Of Missing Out（FOMO）…是指…` 必須以完整定義納入摘要，並同時顯示「強調」與「縮略語」訊號。
11. 「先…再…」處置鏈不得輸出只含後半段的懸空子句。
12. 品質回歸至少涵蓋定義完整性、無關日期、低重疊首句偏誤、否定誤判、硬預算與語意懸空六類案例。
