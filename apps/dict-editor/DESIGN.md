# LingXi Lexicon Studio — UI 與元件規範

## Primary task

讓語言模型維護者能在一個中央工作區內快速找到詞條、判讀標註、完成修改，並安全地寫回相容於舊版 LingXi 的 JSON 詞典。

## Task model

| 層級 | 目標 |
|---|---|
| Primary goal | 搜尋、檢視與編輯目前詞典的詞條 |
| Secondary goal | 新增詞條、批次標未知、批次刪除、全域字串代換與儲存 |
| Low-frequency goal | 維護分詞訓練資料、匯出潛在新詞 |
| Rare goal | 處理格式錯誤、代換衝突、瀏覽器無法直接覆寫原檔的情況 |

## User flow

```text
開啟詞典
  → 解析與驗證格式
  → 搜尋／篩選詞條
  → 單筆編輯或批次操作
  → 顯示未儲存狀態
  → 儲存回原檔（支援時）或下載新檔
```

訓練資料維護為次要模式：

```text
切換至訓練資料
  → 開啟 TXT
  → 瀏覽／查詢 100 筆
  → 代換或刪除
  → 儲存資料與下載潛在新詞
```

## State model

| State | 進入條件 | 必顯資訊 | 隱藏資訊 | Primary CTA | 離開條件 |
|---|---|---|---|---|---|
| `empty` | 尚未載入檔案 | 檔案用途、支援格式、開啟按鈕 | 表格、批次工具、分頁 | 開啟詞典 | 檔案選取完成 |
| `loading` | 正在讀取或解析大型檔案 | 檔名、處理中狀態 | 所有編輯操作 | 等待解析 | 解析成功或失敗 |
| `ready` | 已載入且無待存修改 | 搜尋、篩選、表格、檔案摘要 | 例外處理 | 新增詞條／開始編輯 | 內容被修改 |
| `dirty` | 有未儲存修改 | 待儲存標記、儲存按鈕、工作區 | 空狀態與格式說明 | 儲存變更 | 儲存成功或重新載入 |
| `saving` | 正在序列化或寫檔 | 儲存進度與檔名 | 破壞性操作 | 等待完成 | 成功或失敗 |
| `error` | 檔案格式、讀寫或操作失敗 | 明確原因與復原方法 | 無關參考資訊 | 重新選檔／重試 | 問題修復 |

## Information architecture

| 資訊項目 | 角色 | 頻率 | 首屏必須 | 顯示條件 | 容器 | 可收合 |
|---|---|---:|---:|---|---|---:|
| 開啟／儲存與目前檔名 | action-critical | 高 | 是 | 永遠 | Top action bar | 否 |
| 搜尋與欄位篩選 | action-critical | 高 | 是 | 詞典已載入 | Inline toolbar | 否 |
| 詞條表格 | action-critical | 高 | 是 | 詞典已載入 | Main stage | 否 |
| 選取項目批次操作 | action-critical | 中 | 否 | 有選取項目 | Sticky selection bar | 否 |
| 新增／編輯表單 | action-critical | 中 | 否 | 使用者啟動 | Modal dialog | 否 |
| 全域代換 | exception-handling | 低 | 否 | 使用者啟動 | Modal dialog | 否 |
| 格式錯誤與衝突 | exception-handling | 低 | 否 | 發生錯誤 | Inline alert / modal | 否 |
| 檔案統計與目前頁碼 | status-feedback | 高 | 是 | 檔案已載入 | Table footer | 否 |
| 詞性／實體／情感說明 | reference | 低 | 否 | 控制項 focus 或主動查看 | Native option label / help dialog | 是 |
| 訓練資料維護 | decision-supporting | 低 | 否 | 切換模式 | Secondary workspace tab | 否 |
| 潛在新詞清單 | audit/history | 低 | 否 | 已產生新詞 | Drawer | 是 |

## Content audit

- `must-see-now`：目前檔案、儲存狀態、搜尋、篩選、詞條表格、分頁。
- `next-step-only`：選取批次列、新增／編輯對話框、訓練資料代換表單。
- `error-only`：解析失敗、重複詞條、代換衝突、寫檔權限不足。
- `on-demand-reference`：標註代碼說明、瀏覽器儲存方式、潛在新詞。
- `keep-off-first-viewport`：長篇格式說明、完整標註表、歷史操作紀錄。

## Deferred blocks

| id | hidden_now_because | reveal_trigger | container |
|---|---|---|---|
| `entry-editor` | 未選擇新增或編輯動作時不影響判讀 | 點擊「新增詞條」或列尾編輯 | Modal |
| `replace-tool` | 全域代換低頻且可能大量修改 | 點擊「批次代換」 | Modal |
| `bulk-actions` | 沒有選取詞條時沒有可操作對象 | 選取至少一列 | Sticky bar |
| `new-word-drawer` | 新詞清單只在訓練資料代換後有意義 | 點擊新詞計數 | Side drawer |
| `error-detail` | 正常狀態不應佔用主舞台 | 解析、驗證或寫檔失敗 | Inline alert |

## Block metadata

```json
[
  {
    "id": "lexicon-table",
    "role": "action-critical",
    "priority": "high",
    "visibility": "ready-or-dirty",
    "stage": ["ready", "dirty"],
    "container": "main-stage"
  },
  {
    "id": "save-state",
    "role": "status-feedback",
    "priority": "high",
    "visibility": "always",
    "stage": ["empty", "loading", "ready", "dirty", "saving", "error"],
    "container": "top-action-bar"
  },
  {
    "id": "format-error",
    "role": "exception-handling",
    "priority": "high",
    "visibility": "conditional",
    "stage": ["error"],
    "container": "inline-alert"
  }
]
```

## Design direction

採「編輯台上的紙與墨」方向：暖灰紙張背景、深墨藍主結構、薄荷綠代表可執行動作、珊瑚紅只用於破壞性操作。大面積表格保持安靜，狀態與選取以細線、底色和固定位置呈現，不使用漸層文字、玻璃擬態或卡片農場。

## Design tokens

- 色彩：`--color-canvas`、`--color-surface`、`--color-ink`、`--color-muted`、`--color-line`、`--color-primary`、`--color-danger` 與語意狀態色。
- 字體：繁中介面使用 Noto Sans TC / PingFang TC / Microsoft JhengHei；代碼與數字使用 JetBrains Mono / Consolas。
- 間距：4、8、12、16、24、32、48。
- 圓角：6、10、14、999。
- 動效：140ms、220ms；遵守 `prefers-reduced-motion`。

## Reusable component guideline

### Usage

- `.button` 用於可執行動作；一個視窗同時只保留一個 `.button--primary`。
- `.field` 統一文字輸入、選單與數字輸入的 label、helper、error 結構。
- `.data-table` 只渲染目前頁面，避免大型詞典造成 DOM 膨脹。
- `.modal` 承載新增、編輯、代換與確認，不在首屏永久佔位。

### Layout

- Desktop：top action bar + mode navigation + central editor。
- Tablet：次要欄位縮短，工具列允許換行。
- Mobile：保留搜尋、詞語、詞性、唯讀詞頻與編輯；實體、情感移入編輯 modal。

### Anatomy

- Button：icon、task-specific label、optional count。
- Field：label、control、helper/error。
- Table row：selection、word、tag、entity、emotion、read-only frequency、row action。
- 詞頻不是人工維護欄位；新增時固定使用預設值，僅由語料批次回寫一次。
- 批次先正規化異體字；同一 canonical 詞形（例如 `台積電`／`臺積電`）共用候選句、取樣狀態與詞頻。
- Modal：title、context copy、form/body、secondary action、primary action。

### States & spec

- 互動元件皆提供 default、hover、active、focus-visible、disabled、loading。
- 工作區提供 empty、loading、ready、dirty、saving、error。
- Focus ring 使用 3px 淡薄荷外框；危險操作使用珊瑚色但仍維持 AA 對比。

### Interaction

- 搜尋輸入延遲 160ms 更新。
- `Ctrl/Cmd + S` 儲存；`/` 聚焦搜尋；`Escape` 關閉 modal／drawer。
- 批次刪除永遠二次確認；代換先顯示影響筆數與衝突數。
- 支援 File System Access API 時覆寫原檔；否則下載新檔。

### Content / asset

- CTA 使用「開啟詞典」「儲存變更」「刪除 12 筆」等任務詞，不使用「OK」「Submit」。
- 錯誤訊息同時說明原因與下一步。
- 圖示使用內嵌 SVG，無外部字型或影像依賴。
