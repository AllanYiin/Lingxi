import {
  activeSignals,
  buildCumulativeCurve,
  curvePath,
  formatDuration,
  formatScore,
  reductionLabel
} from "./core.js";

const form = document.querySelector("#summary-form");
const sourceText = document.querySelector("#source-text");
const runButton = document.querySelector("#run-button");
const sampleButton = document.querySelector("#sample-button");
const characterCount = document.querySelector("#character-count");
const runStatus = document.querySelector("#run-status");
const errorMessage = document.querySelector("#error-message");
const results = document.querySelector("#results");
const tabs = [...document.querySelectorAll('[role="tab"]')];

const sampleText = `LingXi 的摘要測試必須完全在本機執行，不得呼叫任何 LLM，也不能改寫原文。

- 專有名詞：OpenAI 與臺灣大學僅作測試樣本。
- 強調規則：**不可省略否定條件**，並保留「使用者明確指定」的內容。
- 物件名稱：請檢查 \`extract_summary()\`、functions.exec 與 apply_patch。
- 日期與數值：2026-08-20 公布結果，風險增加 15%。

Fear Of Missing Out（FOMO）在投資市場，是看到別人賺錢、自己沒跟上而感到焦慮。

一般背景資訊可以依重要性降低權重；但括號內的限制（例如：不得外送資料，不能產生新句子）仍需保留。最後，系統應以可解釋性門檻決定摘要長度。`;

sourceText.addEventListener("input", updateInputState);
sampleButton.addEventListener("click", () => {
  sourceText.value = sampleText;
  updateInputState();
  sourceText.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = sourceText.value;
  if (!text.trim()) return;
  setRunning(true);
  errorMessage.hidden = true;
  runStatus.textContent = "正在本機執行區塊解析、階層式排名與 tiktoken 計數……";
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        maxBlocks: Number(document.querySelector("#max-blocks").value),
        minExplainability: Number(document.querySelector("#min-explainability").value)
      })
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || "本機分析失敗");
    renderReport(report);
    runStatus.textContent = `完成：拆成 ${report.blockCount} 個區塊，納入 ${report.output.selectedBlocks} 個排名段落；另完整保留 ${report.budget.preservedBlocks} 個結構區塊。`;
    results.hidden = false;
    results.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  } catch (error) {
    errorMessage.textContent = `${error.message} 請確認已執行 cargo build -p lingxi-cli，且 LINGXI_ASSETS 指向有效模型。`;
    errorMessage.hidden = false;
    runStatus.textContent = "執行未完成；原文仍保留在輸入框，可修正後重試。";
  } finally {
    setRunning(false);
  }
});

for (const tab of tabs) {
  tab.addEventListener("click", () => activateTab(tab));
  tab.addEventListener("keydown", (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const index = tabs.indexOf(tab);
    const target = event.key === "Home" ? tabs[0]
      : event.key === "End" ? tabs.at(-1)
      : tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    activateTab(target);
    target.focus();
  });
}

function updateInputState() {
  const count = [...sourceText.value].length;
  characterCount.textContent = `${count.toLocaleString("zh-TW")} 字元`;
  runButton.disabled = count === 0;
  if (count > 0 && !form.classList.contains("is-running")) runStatus.textContent = "內容只會送到本機 LingXi CLI。";
}

function setRunning(running) {
  form.classList.toggle("is-running", running);
  sourceText.readOnly = running;
  runButton.disabled = running || !sourceText.value.trim();
  runButton.querySelector(".button-label").textContent = running ? "計算中" : "執行";
  sampleButton.disabled = running;
  runButton.setAttribute("aria-busy", String(running));
}

function activateTab(activeTab) {
  for (const tab of tabs) {
    const active = tab === activeTab;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    document.querySelector(`#${tab.getAttribute("aria-controls")}`).hidden = !active;
  }
}

function renderReport(report) {
  document.querySelector("#input-tokens").textContent = report.input.tokens.toLocaleString("zh-TW");
  document.querySelector("#output-tokens").textContent = report.output.tokens.toLocaleString("zh-TW");
  document.querySelector("#reduction-percent").textContent = reductionLabel(report.input.tokens, report.output.tokens);
  document.querySelector("#elapsed-time").textContent = formatDuration(report.elapsedMs);
  document.querySelector("#engine-stamp").textContent = `${report.engine} / ${report.llmCalls} LLM / ${report.tokenizer}`;
  document.querySelector("#before-text").textContent = report.input.text;
  document.querySelector("#block-count").textContent = report.blockCount;
  document.querySelector("#selected-count").textContent = report.output.selectedBlocks;
  document.querySelector("#after-title").textContent = "結構感知摘要";
  const afterRatio = report.input.tokens ? Math.min(100, report.output.tokens / report.input.tokens * 100) : 0;
  document.querySelector("#token-ruler-after").style.width = `${afterRatio}%`;
  renderSummary(report);
  renderCurve(report.blocks);
  renderBlocks(report.blocks);
  activateTab(document.querySelector("#tab-summary"));
}

function renderSummary(report) {
  const container = document.querySelector("#after-text");
  container.replaceChildren();
  if (report.budget.overflowReasons.length) {
    const note = document.createElement("p");
    note.className = "preserve-all-note";
    note.textContent = report.budget.overflowReasons.join("；");
    container.append(note);
  }
  const selected = report.blocks.filter((block) => block.decision !== "omit");
  if (!selected.length) {
    const empty = document.createElement("p");
    empty.className = "empty-summary";
    empty.textContent = `沒有段落達到 ${report.settings.minExplainability.toFixed(2)} 的可解釋性門檻，也沒有必須完整保留的結構區塊。`;
    container.append(empty);
    return;
  }
  for (const item of selected) {
    const block = document.createElement("span");
    block.className = "summary-clause";
    const text = document.createElement("span");
    text.textContent = item.outputText;
    block.append(text);
    appendSignalTags(block, item.signals, "summary-clause__signals");
    container.append(block);
  }
}

function renderCurve(blocks) {
  const points = buildCumulativeCurve(blocks);
  const path = curvePath(points);
  document.querySelector("#curve-line").setAttribute("d", path);
  const area = path ? `${path} L692,192 L28,192 Z` : "";
  document.querySelector("#curve-area").setAttribute("d", area);
  const group = document.querySelector("#curve-points");
  group.replaceChildren();
  const usableWidth = 664;
  const usableHeight = 164;
  points.forEach((point, index) => {
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    const x = points.length === 1 ? 360 : 28 + index / (points.length - 1) * usableWidth;
    const y = 192 - point.cumulativeShare * usableHeight;
    circle.setAttribute("cx", String(x));
    circle.setAttribute("cy", String(y));
    circle.setAttribute("r", "4");
    circle.setAttribute("class", `curve-point${point.selected ? " is-selected" : ""}`);
    group.append(circle);
  });
  document.querySelector("#curve-description").textContent = points.length
    ? `共 ${points.length} 個可排名區塊；最後累積至 100%，實心點代表被選入摘要。`
    : "沒有可排名區塊。";
}

function renderBlocks(blocks) {
  const list = document.querySelector("#block-list");
  list.replaceChildren();
  for (const block of blocks) {
    const row = document.createElement("div");
    row.className = `clause-row${block.decision !== "omit" ? " is-selected" : ""}`;
    row.setAttribute("role", "row");

    const number = document.createElement("span");
    number.className = "clause-number";
    number.setAttribute("role", "cell");
    number.textContent = String(block.index + 1).padStart(2, "0");

    const copy = document.createElement("div");
    copy.className = "clause-copy";
    copy.setAttribute("role", "cell");
    const text = document.createElement("p");
    text.textContent = block.sourceText;
    copy.append(text);
    appendSignalTags(copy, block.signals, "signal-list");

    const importance = scoreCell("relevance", block.score?.relevance, "score-cell--importance");
    const explainability = scoreCell("final score", block.score?.finalScore, "score-cell--explain");
    const selection = document.createElement("span");
    selection.className = `selection-state${block.decision !== "omit" ? " is-selected" : ""}`;
    selection.setAttribute("role", "cell");
    selection.textContent = block.decision;
    row.append(number, copy, importance, explainability, selection);
    list.append(row);
  }
}

function scoreCell(label, value, className) {
  const cell = document.createElement("span");
  cell.className = `score-cell ${className}`;
  cell.setAttribute("role", "cell");
  cell.setAttribute("aria-label", `${label} ${formatScore(value)}`);
  const score = document.createElement("span");
  score.textContent = formatScore(value);
  const track = document.createElement("span");
  track.className = "score-track";
  const fill = document.createElement("span");
  fill.style.width = `${Number.isFinite(value) ? Math.max(0, Math.min(1, value)) * 100 : 0}%`;
  track.append(fill);
  cell.append(score, track);
  return cell;
}

function appendSignalTags(container, signals, className) {
  const active = activeSignals(signals);
  if (!active.length) return;
  const list = document.createElement("span");
  list.className = className;
  for (const signal of active) {
    const tag = document.createElement("span");
    tag.className = `signal-tag ${signal.className}`;
    const value = signal.key === "listItem" ? "" : ` ×${signals[signal.key]}`;
    tag.textContent = `${signal.label}${value}`;
    list.append(tag);
  }
  container.append(list);
}

updateInputState();
