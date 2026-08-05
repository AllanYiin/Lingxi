import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  applySampledFrequencies,
  bootstrapState,
  countWordsAcrossCorpora,
  listPendingWords,
  parseDictionary,
  writeJsonAtomically
} from "../batch/frequency-core.mjs";
import {
  buildSamplingPlan,
  countAcceptedTokens,
  createNormalizer,
  extractCandidateSentences
} from "../batch/candidate-sampling.mjs";


async function fixtureDirectory() {
  return mkdtemp(join(tmpdir(), "lingxi-frequency-"));
}

test("三份語料以分詞 token 原樣出現次數加總，不計跨 token 字串", async () => {
  const root = await fixtureDirectory();
  const paths = [join(root, "a.txt"), join(root, "b.txt"), join(root, "c.txt")];
  await Promise.all([
    writeFile(paths[0], "\uFEFF台積電|很好\n台積|電|台積電\n", "utf8"),
    writeFile(paths[1], "台積電|台積電|新聞\n", "utf8"),
    writeFile(paths[2], "PTT|台積|電\n", "utf8")
  ]);

  const result = await countWordsAcrossCorpora(paths, ["台積電", "台積", "不存在"]);
  assert.deepEqual(Object.fromEntries(result.counts), {
    台積電: 4,
    台積: 2,
    不存在: 0
  });
  assert.equal(result.sources.length, 3);
  assert.equal(result.sources.reduce((total, source) => total + source.lines, 0), 4);
});

test("既有詞條 bootstrap 後不再重算，只有新詞進入待處理清單", () => {
  const dictionary = parseDictionary('{"既有詞":["n",9]}');
  const state = bootstrapState(dictionary, "2026-08-01T00:00:00.000Z");
  dictionary["新詞"] = ["unknownnew", 1];

  assert.deepEqual(listPendingWords(dictionary, state), ["新詞"]);
  const first = applySampledFrequencies(dictionary, state, new Map([["新詞", 7]]), {
    sampledAt: "2026-08-01T01:00:00.000Z",
    batchId: "test"
  });
  const second = applySampledFrequencies(dictionary, state, new Map([["新詞", 99]]));

  assert.deepEqual(first, [{ word: "新詞", aliases: ["新詞"], frequency: 7 }]);
  assert.deepEqual(second, []);
  assert.equal(dictionary["新詞"][1], 7);
});

test("台與臺正規化為同一詞，沿用同詞性的中位數並一起回寫", () => {
  const normalize = createNormalizer();
  const dictionary = parseDictionary(
    '{"台積電":["nt",1],"臺積電":["nt",1],"甲公司":["nt",2],"乙公司":["nt",4],"丙公司":["nt",6]}'
  );
  const state = bootstrapState(dictionary, "2026-08-01T00:00:00.000Z", normalize);
  delete state.sampled["台積電"];

  const plan = buildSamplingPlan(dictionary, state, normalize);
  assert.deepEqual(plan, [
    {
      canonical: "台積電",
      aliases: ["台積電", "臺積電"],
      tag: "nt",
      provisionalFrequency: 4
    }
  ]);
  applySampledFrequencies(dictionary, state, new Map([["台積電", 10]]), { normalize });
  assert.equal(dictionary["台積電"][1], 10);
  assert.equal(dictionary["臺積電"][1], 10);
});

test("先找正規化候選句，再只計算模型輸出的完整 canonical token", async () => {
  const normalize = createNormalizer();
  const root = await fixtureDirectory();
  const corpus = join(root, "corpus.txt");
  const candidates = join(root, "candidates.txt");
  const segmented = join(root, "segmented.jsonl");
  await writeFile(
    corpus,
    "看好|台積|電|。\n買|臺|積電|股票\n唯一|能|跟|台積|電競|爭\n",
    "utf8"
  );
  const source = await extractCandidateSentences(
    corpus,
    candidates,
    ["台積電"],
    normalize
  );
  await writeFile(
    segmented,
    [
      { tokens: [{ w: "看好" }, { w: "台積電" }, { w: "。" }] },
      { tokens: [{ w: "買" }, { w: "臺積電" }, { w: "股票" }] },
      { tokens: [{ w: "唯一" }, { w: "能" }, { w: "跟" }, { w: "台積" }, { w: "電競" }, { w: "爭" }] }
    ].map((row) => JSON.stringify(row)).join("\n") + "\n",
    "utf8"
  );
  const result = await countAcceptedTokens(segmented, ["台積電"], normalize);

  assert.equal(source.candidateLines, 3);
  assert.equal(source.rawOccurrences.get("台積電"), 3);
  assert.equal(source.originalTokenOccurrences.get("台積電"), 0);
  assert.equal(result.accepted.get("台積電"), 2);
  assert.equal(result.outputLines, 3);
});
test("JSON 回寫可安全取代既有檔案", async () => {
  const root = await fixtureDirectory();
  const path = join(root, "state.json");
  await writeFile(path, '{"version":1}\n', "utf8");
  await writeJsonAtomically(path, { version: 2 });
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), { version: 2 });
});
test("未達門檻時可保留待處理詞，執行後保留其他欄位並只回寫一次", async () => {
  const root = await fixtureDirectory();
  const corpora = [join(root, "a.txt"), join(root, "b.txt"), join(root, "c.txt")];
  await Promise.all(corpora.map((path) => writeFile(path, "新詞|其他\n", "utf8")));
  const dictionary = parseDictionary('{"既有詞":["n",9]}');
  const state = bootstrapState(dictionary, "2026-08-01T00:00:00.000Z");
  dictionary["新詞"] = ["unknownnew", 1, "Catchword", "Happy"];

  const pending = listPendingWords(dictionary, state);
  assert.equal(pending.length < 2, true);
  const result = await countWordsAcrossCorpora(corpora, pending);
  applySampledFrequencies(dictionary, state, result.counts, {
    sampledAt: "2026-08-01T01:00:00.000Z",
    batchId: "test-force"
  });

  assert.deepEqual(dictionary["新詞"], ["unknownnew", 3, "Catchword", "Happy"]);
  assert.deepEqual(listPendingWords(dictionary, state), []);
});