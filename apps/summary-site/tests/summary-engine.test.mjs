import assert from "node:assert/strict";
import test from "node:test";

import { summarize } from "../app/summary-engine.ts";

test("summarizes an OpenAI compaction article as prose blocks, not individual lines", () => {
  const text = [
    "OpenAI 的 compaction 會把長對話壓縮成新的輸入項目，目的是在接近 context window 限制前保留後續推理所需資訊。",
    "COMPACTION(C1)",
    "C1 | ← CACHE HIT",
    "這個 item 是相同的，因此它可以屬於 exact prefix 的一部分。",
    "SYSTEM + TOOLS + C1",
    "而且目前 responses/compact 自己也直接支援 prompt_cache_key 與 prompt_cache_options，並回報 cached_tokens 與 cache_write_tokens，所以壓縮的輸入本身也能利用 Prompt Cache。",
    "真正影響成本的是 compaction 前後的 cache read、cache write 與 replay 行為，不能只看單次請求的 input tokens。",
    "因此 auto_compact_limit 不宜設得過低，否則頻繁壓縮可能增加延遲與寫入成本。",
  ].join("\n");

  const report = summarize(text, 5);
  assert.equal(report.selected.length, 3);
  assert.doesNotMatch(report.summary, /COMPACTION\(C1\)|CACHE HIT|SYSTEM \+ TOOLS/);
  assert.match(report.summary, /responses\/compact/);
  assert.match(report.summary, /真正影響成本/);
  assert.match(report.summary, /auto_compact_limit/);
  assert.match(report.selected.at(-1).text, /responses\/compact[\s\S]*真正影響成本[\s\S]*auto_compact_limit/);
  assert.ok(report.selected.some((item) => item.reasons.includes("程式碼脈絡")));
});

test("treats the requested count as an upper bound instead of padding with code", () => {
  const text = [
    "研究結論顯示，過早壓縮會增加 cache write 成本，因此應依實際使用量設定門檻。",
    "```ts",
    "const next = compact(previous);",
    "if (next.cached_tokens === 0) return retry();",
    "```",
    "[1]: https://example.com/reference",
  ].join("\n");

  const report = summarize(text, 5);
  assert.equal(report.selected.length, 1);
  assert.match(report.summary, /^研究結論顯示/);
  assert.doesNotMatch(report.summary, /const next|cached_tokens|https?:\/\//);
  assert.ok(report.selected[0].reasons.includes("程式碼脈絡"));
});

test("labels only a complete acronym definition as an acronym definition", () => {
  const text = [
    "COMPACTION(C1)",
    "Prompt Cache（PC）是指重用相同輸入前綴，以降低重複處理成本。",
  ].join("\n");

  const report = summarize(text, 2);
  assert.equal(report.selected.length, 1);
  assert.deepEqual(report.selected[0].reasons, ["縮略語定義", "程式碼脈絡"]);
  assert.match(report.summary, /是指重用相同輸入前綴/);
});

test("keeps a prose paragraph intact instead of selecting sentences from it", () => {
  const paragraph = [
    "第一個實驗顯示壓縮後仍可重用相同前綴。",
    "第二個實驗則指出，過度壓縮會增加寫入成本。",
    "因此實務上應同時觀察延遲與 cache token 指標。",
  ].join("\n");

  const report = summarize(paragraph, 1);
  assert.equal(report.selected.length, 1);
  assert.equal(report.summary, paragraph);
});

test("returns no prose summary for code-only input", () => {
  const report = summarize([
    "```ts",
    "const next = compact(previous);",
    "return next.cached_tokens;",
    "```",
  ].join("\n"), 5);

  assert.equal(report.summary, "");
  assert.equal(report.selected.length, 0);
});

test("keeps a short standalone prose sentence", () => {
  const report = summarize("服務已恢復。", 5);
  assert.equal(report.summary, "服務已恢復。");
  assert.equal(report.selected.length, 1);
});
