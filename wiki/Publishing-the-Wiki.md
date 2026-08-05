# 發布至 GitHub Wiki

## Goal｜任務目標

本頁說明如何把主 repository 的 `wiki/*.md` 發布到 `AllanYiin/Lingxi` 的 GitHub Wiki。GitHub Wiki 本身是一個獨立 Git repository，因此修改主 repository 的 `wiki/` 目錄不會自動上線；必須把檔案同步並推送到 `Lingxi.wiki.git` 的預設分支。

## Prerequisites / Requirements｜前置條件

- 具有 `AllanYiin/Lingxi` repository 的 Wiki 寫入權限。
- 本機已安裝 Git，且可對 GitHub 完成驗證。
- 主 repository 中的 Wiki 原稿已完成審閱。
- 確認只發布 Markdown；不得把模型、語料、wheel、WASM bundle 或 `dist/` 產物帶入 Wiki repository。

## Procedure｜操作步驟

### Step 1：首次建立 Wiki

如果 repository 尚未建立任何 Wiki 頁面：

1. 開啟 `https://github.com/AllanYiin/Lingxi/settings`。
2. 在 **Features** 確認 **Wikis** 已啟用。
3. 進入 repository 的 **Wiki** 分頁，選擇 **New Page**。
4. 建立一個暫時首頁並按 **Save Page**。

GitHub 官方要求先透過網頁建立第一頁，之後才能複製 Wiki Git repository。若 `git clone` 回報 repository not found，先檢查這一步與目前帳號權限。

### Step 2：從主 repository 發布

以下 PowerShell 指令假設目前位於 LingXi 主 repository 根目錄：

```powershell
git clone https://github.com/AllanYiin/Lingxi.wiki.git ..\Lingxi.wiki
Copy-Item .\wiki\*.md ..\Lingxi.wiki\ -Force
git -C ..\Lingxi.wiki status --short
git -C ..\Lingxi.wiki add .
git -C ..\Lingxi.wiki commit -m "docs: publish LingXi Wiki"
git -C ..\Lingxi.wiki push
```

執行 `git add` 前，必須先閱讀 `status --short`，確認變更只有預期的 Wiki 頁面。若 Wiki repository 已存在，改用：

```powershell
git -C ..\Lingxi.wiki pull --ff-only
Copy-Item .\wiki\*.md ..\Lingxi.wiki\ -Force
git -C ..\Lingxi.wiki status --short
```

檢查無誤後再 commit 與 push。只有推送到 Wiki 預設分支的內容會呈現在網站上。

### Step 3：發布後驗證

1. 開啟 `https://github.com/AllanYiin/Lingxi/wiki`。
2. 確認首頁可讀，且側邊欄已出現。
3. 逐一開啟「功能介紹」「核心原理」「發布至 GitHub Wiki」。
4. 檢查頁內連結與指向主 repository 的原始碼連結。
5. 確認頁面沒有洩漏本機路徑、憑證、模型檔或不允許公開的資產資訊。

成功條件是具 repository 權限的帳號能讀取三個主頁、側邊欄導覽正常，而且 Wiki repository 的最新 commit 與本次發布 commit 一致。

## 更新與維護

- 功能或演算法變更時，同步更新 `Feature-Overview.md` 與 `How-LingXi-Works.md`。
- 版本號變更時，同步更新 `Home.md`、各頁適用版本與 footer。
- 頁面重新命名時，同步更新 `_Sidebar.md` 及所有相對連結。
- 先在主 repository 維護原稿，再同步到 Wiki repository，避免線上內容成為唯一版本。
- GitHub 以檔名決定頁面名稱；檔名不得含 `\ / : * ? " < > |`。

## 回退

若發布後發現內容錯誤，在 Wiki repository 使用可追蹤的 revert：

```powershell
git -C ..\Lingxi.wiki log --oneline -5
git -C ..\Lingxi.wiki revert <錯誤發布的-commit-sha>
git -C ..\Lingxi.wiki push
```

推送後重新執行發布後驗證。不要以強制推送改寫 Wiki 歷史，除非 repository owner 已明確批准且理解影響。

## Troubleshooting｜常見問題

### `repository not found`

先確認 Wiki 已啟用且至少透過網頁儲存過一頁，再確認目前 GitHub 帳號具有存取權。

### push 成功但頁面沒有更新

確認 commit 已推到 Wiki 的預設分支。GitHub 允許建立其他 Wiki 分支，但只有預設分支會發布。

### 側邊欄沒有顯示

確認檔名是 `_Sidebar.md`，大小寫與前導底線都正確。

### 主 repository 已更新，但 Wiki 還是舊內容

這是預期行為。兩者是獨立 Git repository，必須重新複製 `wiki/*.md`、commit 並 push。

## GitHub 官方文件

- [About wikis](https://docs.github.com/en/communities/documenting-your-project-with-wikis/about-wikis)
- [Adding or editing wiki pages](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)
- [Creating a footer or sidebar for your wiki](https://docs.github.com/en/communities/documenting-your-project-with-wikis/creating-a-footer-or-sidebar-for-your-wiki)
- [Disabling wikis](https://docs.github.com/en/communities/documenting-your-project-with-wikis/disabling-wikis)
