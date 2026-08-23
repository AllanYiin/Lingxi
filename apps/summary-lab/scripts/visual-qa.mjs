import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const packageRoot = process.env.PLAYWRIGHT_PACKAGE_ROOT;
if (!packageRoot) {
  throw new Error("請設定 PLAYWRIGHT_PACKAGE_ROOT 指向 Playwright package 目錄");
}
const { chromium } = await import(pathToFileURL(join(packageRoot, "index.mjs")));
const targetUrl = process.argv[2] || "http://127.0.0.1:4174";
const screenshotDir = resolve(process.argv[3] || "artifacts");
mkdirSync(screenshotDir, { recursive: true });

const sample = `LingXi 的摘要測試必須完全在本機執行，不得呼叫任何 LLM，也不能改寫原文。

- 專有名詞：OpenAI 與臺灣大學僅作測試樣本。
- 強調規則：**不可省略否定條件**，並保留「使用者明確指定」的內容。
- 物件名稱：請檢查 \`extract_summary()\`、functions.exec 與 apply_patch。
- 日期與數值：2026-08-20 公布結果，風險增加 15%。

Fear Of Missing Out（FOMO）在投資市場，是看到別人賺錢、自己沒跟上而感到焦慮。

一般背景資訊可以依重要性降低權重；但括號內的限制（例如：不得外送資料，不能產生新句子）仍需保留。最後，系統應以可解釋性門檻決定摘要長度。`;

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined
});
const results = [];
for (const spec of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 }
]) {
  const context = await browser.newContext({ viewport: { width: spec.width, height: spec.height }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.goto(targetUrl, { waitUntil: "networkidle" });
  const initial = await page.evaluate(() => {
    const primary = document.querySelector("[data-primary-task]").getBoundingClientRect();
    const button = document.querySelector("#run-button").getBoundingClientRect();
    return {
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      primaryTop: primary.top,
      primaryBottom: primary.bottom,
      buttonTop: button.top,
      buttonBottom: button.bottom,
      viewportHeight: innerHeight
    };
  });
  await page.locator("#source-text").fill(sample);
  await page.getByRole("button", { name: "執行", exact: true }).click();
  await page.locator("#results").waitFor({ state: "visible", timeout: 30_000 });
  const summaryVisible = await page.locator("#panel-summary").isVisible();
  const diagnosticsInitiallyHidden = await page.locator("#panel-diagnostics").isHidden();
  const tokenText = await page.locator("#input-tokens").innerText();
  await page.screenshot({ path: join(screenshotDir, `${spec.name}-summary.png`), fullPage: true });

  await page.getByRole("tab", { name: "句子診斷" }).click();
  const diagnosticsVisible = await page.locator("#panel-diagnostics").isVisible();
  const rows = await page.locator("#clause-list .clause-row").count();
  const taggedRows = await page.locator("#clause-list .signal-tag").count();
  const finalLayout = await page.evaluate(() => ({
    horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    activeTab: document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim(),
    visiblePanels: [...document.querySelectorAll('[role="tabpanel"]')].filter((item) => !item.hidden).length
  }));
  await page.screenshot({ path: join(screenshotDir, `${spec.name}-diagnostics.png`), fullPage: true });
  results.push({
    viewport: spec,
    initial,
    summaryVisible,
    diagnosticsInitiallyHidden,
    diagnosticsVisible,
    tokenText,
    rows,
    taggedRows,
    finalLayout,
    consoleErrors
  });
  await context.close();
}
await browser.close();

let failed = false;
for (const result of results) {
  const primaryStartsInViewport = result.initial.primaryTop >= 0 && result.initial.primaryTop < result.initial.viewportHeight;
  const runButtonInViewport = result.initial.buttonTop >= 0 && result.initial.buttonBottom <= result.initial.viewportHeight;
  const pass = !result.initial.horizontalOverflow
    && !result.finalLayout.horizontalOverflow
    && primaryStartsInViewport
    && runButtonInViewport
    && result.summaryVisible
    && result.diagnosticsInitiallyHidden
    && result.diagnosticsVisible
    && result.finalLayout.visiblePanels === 1
    && result.rows > 0
    && result.taggedRows > 0
    && result.consoleErrors.length === 0;
  failed ||= !pass;
  result.pass = pass;
  result.primaryStartsInViewport = primaryStartsInViewport;
  result.runButtonInViewport = runButtonInViewport;
}
console.log(JSON.stringify(results, null, 2));
if (failed) process.exitCode = 1;
