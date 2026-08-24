import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createSummaryLabServer, runSummaryReport } from "../server.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const executableName = process.platform === "win32" ? "lingxi.exe" : "lingxi";
const releaseBinary = join(repoRoot, "target", "release", executableName);
const debugBinary = join(repoRoot, "target", "debug", executableName);
const binary = existsSync(releaseBinary) ? releaseBinary : debugBinary;
const assetsDir = join(repoRoot, "assets");

test("health endpoint identifies the intended zero-LLM local app", async (context) => {
  const server = createSummaryLabServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  context.after(() => new Promise((resolve) => server.close(resolve)));

  const address = server.address();
  assert.equal(typeof address, "object");
  const response = await fetch(`http://127.0.0.1:${address.port}/api/health`);
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(payload, {
    app: "lingxi-summary-lab",
    status: "ready",
    llmCalls: 0
  });
});

test("real zero-LLM report preserves dense Markdown exactly", async (context) => {
  if (!existsSync(binary) || !existsSync(assetsDir)) {
    context.skip("需要已建置的 lingxi CLI 與本機模型資產");
    return;
  }
  const text = await readFile(new URL("fixtures/dense-markdown.md", import.meta.url), "utf8");
  const report = await runSummaryReport(
    { text, maxClauses: 1, minExplainability: 0.99 },
    { binary, assetsDir }
  );
  assert.equal(report.llmCalls, 0);
  assert.equal(report.mode, "structured-markdown-preserve-all");
  assert.equal(report.output.text, report.input.text);
  assert.equal(report.output.tokens, report.input.tokens);
  assert.equal(report.output.selectedClauses, report.clauseCount);
});

test("real zero-LLM report preserves a parenthesized FOMO definition", async (context) => {
  if (!existsSync(binary) || !existsSync(assetsDir)) {
    context.skip("需要已建置的 lingxi CLI 與本機模型資產");
    return;
  }
  const text = await readFile(new URL("fixtures/fomo.txt", import.meta.url), "utf8");
  const report = await runSummaryReport(
    { text, maxClauses: 1, minExplainability: 0.99 },
    { binary, assetsDir }
  );
  const definition = report.clauses.find((clause) => clause.text.includes("Fear Of Missing Out（FOMO）"));
  assert.equal(report.llmCalls, 0);
  assert.equal(definition.selected, true);
  assert.equal(definition.signals.emphasisCount, 1);
  assert.equal(definition.signals.acronymCount, 1);
  assert.match(definition.text, /是指看到別人賺錢/);
  assert.match(definition.text, /焦慮與恐慌。$/);
  assert.equal(report.output.selectedClauses, 1);
  assert.equal(report.output.text, definition.text);
});
