import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { summarize } from "../app/summary-engine.ts";

function posTokens(text, entries) {
  let cursor = 0;
  return entries.map(([word, tag]) => {
    const start = text.indexOf(word, cursor);
    assert.notEqual(start, -1, `missing POS fixture token: ${word}`);
    cursor = start + word.length;
    return { word, tag, start, end: cursor };
  });
}

test("returns schema v2 and ranks prose by blocks", () => {
  const text = [
    "版權頁記載本書出版於2024年。",
    "作者曾在2019年搬家。",
    "研究核心發現是睡眠品質與記憶鞏固密切相關。",
  ].join("\n\n");
  const report = summarize(text, 1);
  assert.equal(report.schemaVersion, 2);
  assert.equal(report.budget.selectedRankedBlocks, 1);
  assert.match(report.text, /研究核心發現/);
  assert.doesNotMatch(report.text, /2024年/);
});

test("preserves fenced code outside ranked budget", () => {
  const code = "```ts\nconst next = compact(previous);\n```";
  const report = summarize(code, 0);
  assert.equal(report.text, code);
  assert.equal(report.blocks[0].kind, "fenced-code");
  assert.equal(report.blocks[0].decision, "preserve_exact");
  assert.equal(report.budget.preservedBlocks, 1);
});

test("preserves short ordered and unordered list items", () => {
  const text = "1. 第一項。\n2. 第二項。\n\n- Third item.\n- Fourth item.";
  const report = summarize(text, 0);
  assert.equal(report.text, text);
  assert.equal(report.blocks.length, 4);
  assert.ok(report.blocks.every((block) => block.decision === "preserve_exact"));
});

test("keeps nested list items and exposes children", () => {
  const text = "- Parent\n  - Child\n- Next";
  const report = summarize(text, 0);
  assert.equal(report.text, text);
  assert.deepEqual(report.blocks.map((block) => block.depth), [0, 1, 0]);
  assert.equal(report.blocks[0].children[0].sourceText.trim(), "- Child");
});

test("never truncates code embedded in a list item", () => {
  const code = "- Run this example:\n  ```js\n  const value = 1;\n  console.log(value);\n  ```";
  const report = summarize(code, 0);
  assert.equal(report.text, code);
  assert.match(report.blocks[0].outputText, /console\.log\(value\)/);
});

test("summarizes long list prose while keeping marker and negation", () => {
  const text = `- ${"Background details ".repeat(30)}. First stage completed. Do not delete user data. Final cleanup runs later.`;
  const report = summarize(text, 0);
  const block = report.blocks[0];
  assert.equal(block.decision, "summarize_within");
  assert.match(block.outputText, /^- /);
  assert.match(block.outputText, /not delete user data/i);
  assert.ok(block.outputText.length < block.sourceText.length);
});

test("reports portable bilingual signals", () => {
  const report = summarize("Fear Of Missing Out（FOMO）是指焦慮。預算 USD 1,024。", 1);
  const block = report.blocks[0];
  assert.ok(block.signals.properNounCount >= 1);
  assert.ok(block.signals.acronymCount >= 1);
  assert.ok(block.signals.moneyCount >= 1);
});

test("does not classify known false-positive negations", () => {
  const report = summarize("不只、不僅、非常、是否、未來、否則；not only, whether or not, otherwise.", 1);
  assert.equal(report.blocks[0].signals.negationCount, 0);
});

test("keeps an irreducible one-sentence paragraph exact", () => {
  const paragraph = "研究結論顯示，過早壓縮會增加成本，因此應依實際使用量設定門檻。";
  const report = summarize(paragraph, 1);
  assert.equal(report.text, paragraph);
  assert.equal(report.blocks[0].decision, "select_exact");
});

test("compresses the content inside a selected ordinary paragraph", () => {
  const paragraph = [
    "台股近期市值快速提升，市場地位更加受到國際投資人重視，交易量也呈現持續成長。",
    "這段背景主要回顧過去的市場變化與各界評論，並整理近期重要事件。",
    "研究結論顯示，企業獲利成長才是本輪上漲的主要支撐，後續仍應觀察基本面。",
  ].join("");
  const report = summarize(paragraph, 1);
  assert.equal(report.blocks[0].decision, "summarize_within");
  assert.match(report.text, /研究結論顯示/);
  assert.ok(report.outputChars < report.inputChars);
  assert.ok(report.blocks[0].selectedSpans.length < 8);
});

test("keeps decimal values intact during within-paragraph compression", () => {
  const paragraph = "市場背景持續變化，投資人對後續走勢仍在觀望，短期資金也反覆進出，成交量因此呈現明顯波動。結果顯示EPS達14.72元，企業獲利能力與資本支出意願同步提升，基本面仍是後續觀察重點。";
  const report = summarize(paragraph, 1);
  assert.equal(report.blocks[0].decision, "summarize_within");
  assert.match(report.text, /14\.72元/);
  assert.doesNotMatch(report.text, /14[。.!?]?$/);
});

test("uses commas as selectable units inside one long sentence", () => {
  const paragraph = "今年半年報陸續公布，市場整理各公司營收變化，分析師比較毛利率與營業利益率，部分企業獲利明顯成長，另一些企業仍受到庫存調整影響，投資人最後依照基本面重新評估持股方向。";
  const report = summarize(paragraph, 1);
  const block = report.blocks[0];
  assert.equal(block.decision, "summarize_within");
  assert.ok(block.selectedSpans.length < 6);
  assert.ok(report.outputChars < report.inputChars);
  assert.ok(block.selectedSpans.every((span) => paragraph.includes(span.text)));
});

test("compresses the comma-heavy earnings paragraph from the reported case", () => {
  const paragraph = "謝金河接連點名，今年的半年報，EPS超過100元的公司有群聯，宜鼎，緯穎，川湖四家，賺超過一個股本的公司有183家，如果按照EPS排序，到第100名的公司，EPS仍然高達14.72元，台灣的企業獲利能力也隨著快速提升。";
  const report = summarize(paragraph, 1);
  assert.equal(report.blocks[0].decision, "summarize_within");
  assert.ok(report.outputChars < report.inputChars);
  assert.ok(report.blocks[0].selectedSpans.length < 9);
});

test("keeps dense proper-noun enumerations indivisible with LingXi POS", () => {
  const paragraph = "市場回顧今年盤勢與資金輪動，EPS超過100元的公司有群聯，宜鼎，緯穎，川湖四家，其他背景資料則整理產業消息與歷史變化，研究結論顯示企業獲利能力仍是本輪上漲的主要支撐，投資人後續應持續追蹤基本面。";
  const tokens = posTokens(paragraph, [
    ["群聯", "Nc"], ["宜鼎", "Nb"], ["緯穎", "Nb"], ["川湖四家", "Na"],
  ]);
  const report = summarize(paragraph, 1, tokens);
  const block = report.blocks[0];
  assert.equal(block.decision, "summarize_within");
  assert.ok(block.selectedSpans.some((span) => span.text.includes("群聯，宜鼎，緯穎，川湖四家，")));
  assert.match(block.outputText, /群聯，宜鼎，緯穎，川湖四家，/);
  assert.ok(block.signals.modelProperNounCount >= 3);
});

test("uses POS to prune non-semantic adverbs, conjunctions, and interjections", () => {
  const paragraph = "謝金河其實表示，如果企業的獲利持續成長，而且股價真的反映基本面，投資人可以審慎評估啊。";
  const tokens = posTokens(paragraph, [
    ["謝金河", "Nb"], ["其實", "D"], ["如果", "Cbb"], ["的", "DE"], ["而且", "Cbb"], ["真的", "D"], ["啊", "T"],
  ]);
  const report = summarize(paragraph, 1, tokens);
  const block = report.blocks[0];
  assert.equal(block.decision, "compact_pos");
  assert.match(block.outputText, /謝金河/);
  assert.match(block.outputText, /如果/);
  assert.match(block.outputText, /企業的獲利/);
  assert.doesNotMatch(block.outputText, /其實|而且|真的|啊/);
  assert.deepEqual(block.removedTokens.map((token) => token.word), ["其實", "而且", "真的", "啊"]);
});

test("keeps an 80-character paragraph below the within-paragraph threshold", () => {
  const paragraph = `背景說明，${"甲".repeat(69)}，最後結論。`;
  assert.equal([...paragraph].length, 80);
  const report = summarize(paragraph, 1);
  assert.equal(report.blocks[0].decision, "select_exact");
  assert.equal(report.text, paragraph);
});

test("matches the shared Rust and TypeScript golden contract", async () => {
  const fixture = JSON.parse(await readFile(new URL("../../../tests/golden/summary-v2.json", import.meta.url), "utf8"));
  const report = summarize(fixture.input, fixture.maxBlocks);
  assert.equal(report.text, fixture.expectedSummary);
  assert.deepEqual(report.blocks.map((block) => block.kind), fixture.expectedKinds);
  assert.deepEqual(report.blocks.map((block) => block.decision), fixture.expectedDecisions);
  assert.deepEqual(report.blocks.map((block) => block.signals.negationCount), fixture.expectedNegationCounts);
  assert.deepEqual(report.blocks.map((block) => block.signals.moneyCount), fixture.expectedMoneyCounts);
});
