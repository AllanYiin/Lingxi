import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), {
    ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
  }, { waitUntil() {}, passThroughOnException() {} });
}

test("server-renders the LingXi Summary product surface", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /LingXi Summary/);
  assert.match(html, /留下原文重點/);
  assert.match(html, /摘要工具/);
  assert.match(html, /工具說明/);
  assert.match(html, /跳至摘要工具/);
  assert.match(html, /0 LLM/);
  assert.match(html, /產生摘要/);
  assert.match(html, /摘要區塊數/);
  assert.match(html, /區塊抽取/);
  assert.doesNotMatch(html, /Your site is taking shape|codex-preview/);
});

test("removes starter preview assets and keeps finished metadata", async () => {
  const [layout, packageJson] = await Promise.all([
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  assert.match(layout, /零 LLM 繁中摘要/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  await assert.rejects(access(new URL("../app/_sites-preview/SkeletonPreview.tsx", import.meta.url)));
});
