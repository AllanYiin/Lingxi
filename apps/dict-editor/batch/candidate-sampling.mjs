import { createReadStream, createWriteStream } from "node:fs";
import { open, stat } from "node:fs/promises";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { resolve } from "node:path";

export const DEFAULT_VARIANT_MAP = Object.freeze({ 臺: "台" });

export function createNormalizer(variantMap = DEFAULT_VARIANT_MAP) {
  const entries = Object.entries(variantMap);
  for (const [from, to] of entries) {
    if ([...from].length !== 1 || [...to].length !== 1) {
      throw new TypeError("異體字映射的 key 與 value 都必須是單一字元");
    }
  }
  const variants = new Map(entries);
  return (text) =>
    [...String(text)]
      .map((character) => variants.get(character.toLowerCase()) ?? character.toLowerCase())
      .join("");
}

export function countOccurrences(text, target) {
  if (!target) return 0;
  let count = 0;
  let cursor = 0;
  while (cursor <= text.length - target.length) {
    const index = text.indexOf(target, cursor);
    if (index < 0) break;
    count += 1;
    cursor = index + target.length;
  }
  return count;
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

function preferredTag(entries) {
  const specific = entries.find(
    ({ value }) => !["unknown", "unknownnew", "undeterminate"].includes(String(value[0]))
  );
  return String((specific ?? entries[0]).value[0] || "unknownnew");
}

export function buildSamplingPlan(dictionary, state, normalize = createNormalizer()) {
  const groups = new Map();
  for (const [word, value] of Object.entries(dictionary)) {
    const canonical = normalize(word);
    if (!groups.has(canonical)) groups.set(canonical, []);
    groups.get(canonical).push({ word, value });
  }

  const pendingCanonicals = new Set(
    [...groups.keys()].filter(
      (canonical) => !Object.prototype.hasOwnProperty.call(state.sampled, canonical)
    )
  );
  const frequenciesByTag = new Map();
  for (const [canonical, entries] of groups) {
    if (pendingCanonicals.has(canonical)) continue;
    const tag = preferredTag(entries);
    const frequency = Math.max(
      ...entries.map(({ value }) => Math.max(0, Math.trunc(Number(value[1]) || 0)))
    );
    if (frequency <= 0) continue;
    if (!frequenciesByTag.has(tag)) frequenciesByTag.set(tag, []);
    frequenciesByTag.get(tag).push(frequency);
  }

  return [...pendingCanonicals].map((canonical) => {
    const entries = groups.get(canonical);
    const tag = preferredTag(entries);
    const tagMedian = median(frequenciesByTag.get(tag) ?? []);
    return {
      canonical,
      aliases: entries.map(({ word }) => word),
      tag,
      provisionalFrequency: tagMedian ?? 1
    };
  });
}

async function writeLine(writer, line) {
  if (!writer.write(`${line}\n`, "utf8")) await once(writer, "drain");
}

export async function extractCandidateSentences(
  corpusPath,
  candidatePath,
  targetWords,
  normalize = createNormalizer()
) {
  const targets = new Set(targetWords);
  const rawOccurrences = new Map([...targets].map((word) => [word, 0]));
  const originalTokenOccurrences = new Map([...targets].map((word) => [word, 0]));
  let lineCount = 0;
  let tokenCount = 0;
  let candidateLines = 0;
  const writer = createWriteStream(candidatePath, { encoding: "utf8", flags: "wx" });
  const lines = createInterface({
    input: createReadStream(corpusPath, { encoding: "utf8" }),
    crlfDelay: Infinity
  });

  try {
    for await (let line of lines) {
      lineCount += 1;
      if (lineCount === 1) line = line.replace(/^\uFEFF/, "");
      const tokens = line.split("|").filter(Boolean);
      tokenCount += tokens.length;
      for (const token of tokens) {
        const canonicalToken = normalize(token);
        if (targets.has(canonicalToken)) {
          originalTokenOccurrences.set(
            canonicalToken,
            originalTokenOccurrences.get(canonicalToken) + 1
          );
        }
      }

      const plain = tokens.join("");
      const normalizedPlain = normalize(plain);
      let matched = false;
      for (const target of targets) {
        const occurrences = countOccurrences(normalizedPlain, target);
        if (!occurrences) continue;
        rawOccurrences.set(target, rawOccurrences.get(target) + occurrences);
        matched = true;
      }
      if (!matched) continue;
      candidateLines += 1;
      await writeLine(writer, plain);
    }
  } finally {
    writer.end();
    await once(writer, "close");
  }

  const sourceStat = await stat(corpusPath);
  return {
    path: resolve(corpusPath),
    bytes: sourceStat.size,
    modifiedAt: sourceStat.mtime.toISOString(),
    lines: lineCount,
    tokens: tokenCount,
    candidateLines,
    rawOccurrences,
    originalTokenOccurrences
  };
}

export async function countAcceptedTokens(
  segmentedJsonlPath,
  targetWords,
  normalize = createNormalizer()
) {
  const targets = new Set(targetWords);
  const accepted = new Map([...targets].map((word) => [word, 0]));
  let outputLines = 0;
  const lines = createInterface({
    input: createReadStream(segmentedJsonlPath, { encoding: "utf8" }),
    crlfDelay: Infinity
  });
  for await (const line of lines) {
    if (!line) continue;
    outputLines += 1;
    const row = JSON.parse(line);
    if (!Array.isArray(row.tokens)) throw new TypeError("分詞器 JSONL 缺少 tokens 陣列");
    for (const token of row.tokens) {
      const canonical = normalize(token.w);
      if (targets.has(canonical)) accepted.set(canonical, accepted.get(canonical) + 1);
    }
  }
  return { accepted, outputLines };
}

export async function writeUserDictionary(path, plan) {
  const handle = await open(path, "wx");
  try {
    await handle.writeFile(
      `${plan
        .map(
          ({ canonical, provisionalFrequency, tag }) =>
            `${canonical} ${provisionalFrequency} ${tag}`
        )
        .join("\n")}\n`,
      "utf8"
    );
  } finally {
    await handle.close();
  }
}
