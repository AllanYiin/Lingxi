import { createReadStream } from "node:fs";
import { open, rename, stat, unlink } from "node:fs/promises";
import { createInterface } from "node:readline";
import { dirname, resolve } from "node:path";

import { DEFAULT_NEW_ENTRY_FREQUENCY } from "../core.js";
import { createNormalizer } from "./candidate-sampling.mjs";

export const STATE_SCHEMA_VERSION = 2;
const defaultNormalize = createNormalizer();
export { DEFAULT_NEW_ENTRY_FREQUENCY };

function safeFrequency(value, fallback = DEFAULT_NEW_ENTRY_FREQUENCY) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : fallback;
}

export function parseDictionary(text) {
  const dictionary = JSON.parse(String(text).replace(/^\uFEFF/, ""));
  if (!dictionary || typeof dictionary !== "object" || Array.isArray(dictionary)) {
    throw new TypeError("詞典根節點必須是 JSON object");
  }
  for (const [word, value] of Object.entries(dictionary)) {
    if (!word || !Array.isArray(value) || value.length < 2) {
      throw new TypeError(`詞條「${word}」必須是至少包含詞性與詞頻的陣列`);
    }
  }
  return dictionary;
}

export function emptyState() {
  return {
    schemaVersion: STATE_SCHEMA_VERSION,
    sampled: {},
    lastBatch: null
  };
}

export function parseState(text) {
  const state = JSON.parse(String(text).replace(/^\uFEFF/, ""));
  if (
    !state ||
    typeof state !== "object" ||
    state.schemaVersion !== STATE_SCHEMA_VERSION ||
    !state.sampled ||
    typeof state.sampled !== "object" ||
    Array.isArray(state.sampled)
  ) {
    throw new TypeError(`詞頻狀態檔必須符合 schemaVersion ${STATE_SCHEMA_VERSION}`);
  }
  return state;
}

export function listPendingWords(dictionary, state, normalize = defaultNormalize) {
  const canonicalWords = new Set(Object.keys(dictionary).map((word) => normalize(word)));
  return [...canonicalWords].filter(
    (canonical) => !Object.prototype.hasOwnProperty.call(state.sampled, canonical)
  );
}

export function bootstrapState(
  dictionary,
  sampledAt = new Date().toISOString(),
  normalize = defaultNormalize
) {
  const state = emptyState();
  for (const [word, value] of Object.entries(dictionary)) {
    const canonical = normalize(word);
    const frequency = safeFrequency(value[1], 0);
    const current = state.sampled[canonical];
    if (!current || frequency > current.frequency) {
      state.sampled[canonical] = {
        frequency,
        sampledAt,
        batchId: "bootstrap",
        aliases: current ? [...new Set([...current.aliases, word])] : [word]
      };
    } else if (!current.aliases.includes(word)) {
      current.aliases.push(word);
    }
  }
  return state;
}

export async function scanSegmentedCorpus(filePath, targetWords) {
  const counts = new Map([...targetWords].map((word) => [word, 0]));
  let lineCount = 0;
  let tokenCount = 0;
  let matchedTokenCount = 0;
  const input = createReadStream(filePath, { encoding: "utf8" });
  const lines = createInterface({ input, crlfDelay: Infinity });

  for await (let line of lines) {
    lineCount += 1;
    if (lineCount === 1) line = line.replace(/^\uFEFF/, "");
    for (const token of line.split("|")) {
      if (!token) continue;
      tokenCount += 1;
      if (!counts.has(token)) continue;
      counts.set(token, counts.get(token) + 1);
      matchedTokenCount += 1;
    }
  }

  return { filePath, counts, lineCount, tokenCount, matchedTokenCount };
}

export async function countWordsAcrossCorpora(corpusPaths, targetWords) {
  const targets = new Set(targetWords);
  const counts = new Map([...targets].map((word) => [word, 0]));
  const sources = [];

  for (const corpusPath of corpusPaths) {
    const source = await scanSegmentedCorpus(corpusPath, targets);
    const sourceStat = await stat(corpusPath);
    for (const [word, count] of source.counts) {
      counts.set(word, counts.get(word) + count);
    }
    sources.push({
      path: resolve(corpusPath),
      bytes: sourceStat.size,
      modifiedAt: sourceStat.mtime.toISOString(),
      lines: source.lineCount,
      tokens: source.tokenCount,
      matchedTokens: source.matchedTokenCount
    });
  }

  return { counts, sources };
}

export function applySampledFrequencies(
  dictionary,
  state,
  counts,
  {
    sampledAt = new Date().toISOString(),
    batchId = sampledAt,
    normalize = defaultNormalize
  } = {}
) {
  const updated = [];
  for (const [canonical, rawCount] of counts) {
    if (Object.prototype.hasOwnProperty.call(state.sampled, canonical)) continue;
    const aliases = Object.keys(dictionary).filter((word) => normalize(word) === canonical);
    if (!aliases.length) continue;
    const frequency = safeFrequency(rawCount, 0);
    for (const word of aliases) dictionary[word][1] = frequency;
    state.sampled[canonical] = { frequency, sampledAt, batchId, aliases };
    updated.push({ word: canonical, aliases, frequency });
  }
  return updated;
}

export async function writeJsonAtomically(path, value) {
  const absolute = resolve(path);
  const temporary = resolve(
    dirname(absolute),
    `.${absolute.split(/[\\/]/).at(-1)}.${process.pid}.${Date.now()}.tmp`
  );
  const handle = await open(temporary, "wx");
  try {
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await rename(temporary, absolute);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw error;
  }
}
