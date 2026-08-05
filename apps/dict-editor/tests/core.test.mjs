import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_NEW_ENTRY_FREQUENCY,
  applyDictionaryReplacement,
  decodeDictionaryValue,
  deleteFirstExactTrainingLine,
  encodeDictionaryValue,
  filterAndSortRows,
  inferPotentialWord,
  parseDictionaryText,
  previewDictionaryReplacement,
  replaceTrainingLines,
  serializeDictionaryRows,
  derivePolarity,
  parseAffectLexiconText,
  parseCustomLexiconText,
  parseEmotionTaxonomyText,
  previewLegacyEmotionMigration,
  serializeAffectLexicon,
  serializeCustomLexicon
} from "../core.js";

test("新增詞條的系統預設詞頻固定為 1", () => {
  assert.equal(DEFAULT_NEW_ENTRY_FREQUENCY, 1);
});

test("解析舊版二欄、三欄與四欄詞典值", () => {
  assert.deepEqual(decodeDictionaryValue("台積電", ["nt", 12], 1), {
    id: 1,
    word: "台積電",
    tag: "nt",
    frequency: 12,
    entity: "None",
    emotion: "None"
  });
  assert.equal(decodeDictionaryValue("王小明", ["nr", 3, "ChName"], 2).entity, "ChName");
  assert.equal(decodeDictionaryValue("開心", ["a", 5, "Happy"], 3).emotion, "Happy");
  assert.deepEqual(encodeDictionaryValue({
    tag: "n",
    frequency: 8,
    entity: "Catchword",
    emotion: "Happy"
  }), ["n", 8, "Catchword", "Happy"]);
});

test("序列化後仍可由解析器讀回", () => {
  const rows = [
    { id: 1, word: "台積電", tag: "nt", frequency: 100000, entity: "None", emotion: "None" },
    { id: 2, word: "開心", tag: "a", frequency: 20, entity: "None", emotion: "Happy" }
  ];
  const text = serializeDictionaryRows(rows);
  const parsed = parseDictionaryText(text);
  assert.deepEqual(parsed.rows, rows);
});

test("批次代換會跳過重複詞條，避免覆蓋資料", () => {
  const rows = [
    { id: 1, word: "台積", tag: "nt", frequency: 1, entity: "None", emotion: "None" },
    { id: 2, word: "臺積電", tag: "nt", frequency: 2, entity: "None", emotion: "None" }
  ];
  const preview = previewDictionaryReplacement(rows, "臺", "台");
  assert.equal(preview.matchedCount, 1);
  assert.equal(preview.changes.length, 1);
  applyDictionaryReplacement(preview);
  assert.equal(rows[1].word, "台積電");
});

test("訓練資料代換會正規化重複分隔線並推定潛在新詞", () => {
  const result = replaceTrainingLines(["我|愛|台|積|電", "第二行"], "台|積|電", "台積電|");
  assert.equal(result.changedCount, 1);
  assert.equal(result.lines[0], "我|愛|台積電");
  assert.equal(inferPotentialWord("台積電|"), "台積電");
});

test("刪除訓練資料只移除第一筆完全相同內容", () => {
  const result = deleteFirstExactTrainingLine(["A", "B", "A"], "A");
  assert.equal(result.deleted, true);
  assert.deepEqual(result.lines, ["B", "A"]);
});
test("搜尋繁中詞語時保留來源順序，英文字母則不分大小寫", () => {
  const rows = [
    { word: "台北" },
    { word: "PDF檔" },
    { word: "台積電" }
  ];
  assert.deepEqual(
    filterAndSortRows(rows, { query: "台" }, { key: "source" }).map((row) => row.word),
    ["台北", "台積電"]
  );
  assert.deepEqual(
    filterAndSortRows(rows, { query: "pdf" }, { key: "source" }).map((row) => row.word),
    ["PDF檔"]
  );
});

test("結構化自訂辭典不接受 frequency 並可 round-trip", () => {
  const value = parseCustomLexiconText(JSON.stringify({
    schemaVersion: 1,
    id: "medical-tw",
    domain: "medical",
    priority: 3,
    entries: [{ word: "冠狀動脈", pos: "Na" }]
  }));
  assert.equal(value.enabled, true);
  assert.deepEqual(parseCustomLexiconText(serializeCustomLexicon(value)), value);
  assert.throws(() => parseCustomLexiconText(JSON.stringify({
    schemaVersion: 1,
    id: "x",
    domain: "x",
    entries: [{ word: "甲乙", frequency: 9 }]
  })), /未知欄位/);
});

test("情感 taxonomy 支援多標籤與混合極性", () => {
  const taxonomy = parseEmotionTaxonomyText(JSON.stringify({
    schemaVersion: 1,
    version: "1.0.0",
    labels: [
      { id: "joy.joy", family: "joy", nameZhTw: "喜悅", defaultPolarity: "positive" },
      { id: "sadness.sadness", family: "sadness", nameZhTw: "悲傷", defaultPolarity: "negative" }
    ]
  }));
  assert.equal(derivePolarity(["joy.joy", "sadness.sadness"], taxonomy), "mixed");
  const lexicon = parseAffectLexiconText(JSON.stringify({
    schemaVersion: 1,
    taxonomyVersion: "1.0.0",
    entries: [{ word: "百感交集", emotions: ["joy.joy", "sadness.sadness"] }]
  }), taxonomy);
  assert.deepEqual(parseAffectLexiconText(serializeAffectLexicon(lexicon, taxonomy), taxonomy), lexicon);
});

test("舊 18 種 emotion code 全部有遷移結果", () => {
  const codes = [
    "Happy", "Confidence", "Sad", "Annoyed", "Unlike", "NoChoice", "Inferior", "Regret",
    "Blame", "Indifferent", "Disappointed", "Doubt", "Despise", "Worried", "Fake", "Fear",
    "Angry", "Dangerous"
  ];
  const { entries, pending } = previewLegacyEmotionMigration(
    codes.map((emotion, id) => ({ id, word: `詞${id}`, emotion }))
  );
  assert.equal(pending.length, 0);
  assert.equal(entries.length, 18);
  assert.deepEqual(entries.find((entry) => entry.word === "詞14").semanticFlags, ["deception"]);
});
