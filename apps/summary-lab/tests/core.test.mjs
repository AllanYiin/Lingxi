import test from "node:test";
import assert from "node:assert/strict";

import { activeSignals, buildCumulativeCurve, curvePath, reductionLabel } from "../core.js";
import { buildCliArgs, validateAnalyzePayload } from "../server.mjs";

test("payload validation keeps deterministic summary settings bounded", () => {
  assert.deepEqual(validateAnalyzePayload({ text: "測試內容", maxBlocks: 8, minExplainability: 0.4 }), {
    text: "測試內容",
    maxBlocks: 8,
    minExplainability: 0.4
  });
  assert.throws(() => validateAnalyzePayload({ text: " ", maxBlocks: 8 }), /填入/);
  assert.throws(() => validateAnalyzePayload({ text: "內容", minExplainability: 1.2 }), /0 到 1/);
});

test("CLI args only invoke local summary report mode", () => {
  assert.deepEqual(
    buildCliArgs({ maxBlocks: 12, minExplainability: 0.35 }, "D:/assets"),
    ["--assets", "D:/assets", "--summary-report", "12", "--min-explainability", "0.35"]
  );
});

test("signals are explicit labels rather than color-only state", () => {
  const signals = activeSignals({
    properNounCount: 2,
    negationCount: 1,
    listItem: false,
    dateCount: 1,
    numberCount: 2,
    quantityCount: 1,
    acronymCount: 1
  });
  assert.deepEqual(signals.map((signal) => signal.label), [
    "專有名詞",
    "否定",
    "日期",
    "數字",
    "數值＋單位",
    "縮略語"
  ]);
});

test("cumulative curve is monotonic and reaches one", () => {
  const points = buildCumulativeCurve([
    { index: 0, decision: "omit", score: { finalScore: 0.2 } },
    { index: 1, decision: "select_exact", score: { finalScore: 0.8 } },
    { index: 2, decision: "select_exact", score: { finalScore: 0.5 } }
  ]);
  assert.equal(points.length, 3);
  assert.ok(points[1].cumulativeShare >= points[0].cumulativeShare);
  assert.equal(points.at(-1).cumulativeShare, 1);
  assert.match(curvePath(points), /^M/);
});

test("token reduction label compares before and after", () => {
  assert.equal(reductionLabel(100, 35), "65.0%");
});
