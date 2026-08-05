#!/usr/bin/env node

import { mkdtemp, open, readFile, rm, unlink } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  applySampledFrequencies,
  bootstrapState,
  listPendingWords,
  parseDictionary,
  parseState,
  writeJsonAtomically
} from "./frequency-core.mjs";
import {
  buildSamplingPlan,
  countAcceptedTokens,
  createNormalizer,
  extractCandidateSentences,
  writeUserDictionary
} from "./candidate-sampling.mjs";
import { runLingxiSegmenter } from "./segmenter-runner.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "../../..");
const defaultCorpora = [
  "trident-traindata-ckip.txt",
  "news-ckip.txt",
  "ptt-ckip.txt"
].map((name) =>
  resolve(
    repositoryRoot,
    ".corpus-work/model-evaluation/ckip-segmented-sources",
    name
  )
);
const defaultSegmenter = resolve(
  repositoryRoot,
  "target/release",
  process.platform === "win32" ? "lingxi.exe" : "lingxi"
);
const defaultAssets = resolve(repositoryRoot, "assets");

function usage() {
  return `用法：
  node batch/frequency-job.mjs --dictionary <Dict.json> [選項]

選項：
  --state <path>             狀態檔；預設為 <Dict.json>.frequency-state.json
  --corpus <path>            CKIP 分詞語料，可重複三次；省略時使用專案內三份語料
  --threshold <n>            待計算 canonical 新詞達此數量才執行；預設 20
  --segmenter <path>         LingXi CLI；預設 target/release/lingxi
  --assets <path>            LingXi 模型資產；預設專案 assets
  --bootstrap-existing       首次建立狀態，將既有詞條視為已計算且不改詞頻
  --force                    未達門檻仍執行
  --dry-run                  計算但不回寫
  --help                     顯示說明

環境變數：LINGXI_DICTIONARY_PATH、LINGXI_FREQUENCY_THRESHOLD、
          LINGXI_SEGMENTER_PATH、LINGXI_ASSETS`;
}

function parsePositiveInteger(raw, label) {
  const value = Number(raw);
  if (!Number.isInteger(value) || value <= 0) throw new TypeError(`${label} 必須是正整數`);
  return value;
}

function parseArgs(argv) {
  const options = {
    dictionary: process.env.LINGXI_DICTIONARY_PATH || "",
    state: "",
    corpora: [],
    threshold: parsePositiveInteger(process.env.LINGXI_FREQUENCY_THRESHOLD || "20", "門檻"),
    segmenter: process.env.LINGXI_SEGMENTER_PATH || defaultSegmenter,
    assets: process.env.LINGXI_ASSETS || defaultAssets,
    bootstrapExisting: false,
    force: false,
    dryRun: false,
    help: false
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--dictionary") options.dictionary = argv[++index] || "";
    else if (argument === "--state") options.state = argv[++index] || "";
    else if (argument === "--corpus") options.corpora.push(argv[++index] || "");
    else if (argument === "--threshold") {
      options.threshold = parsePositiveInteger(argv[++index], "門檻");
    } else if (argument === "--segmenter") options.segmenter = argv[++index] || "";
    else if (argument === "--assets") options.assets = argv[++index] || "";
    else if (argument === "--bootstrap-existing") options.bootstrapExisting = true;
    else if (argument === "--force") options.force = true;
    else if (argument === "--dry-run") options.dryRun = true;
    else if (argument === "--help" || argument === "-h") options.help = true;
    else throw new TypeError(`未知參數：${argument}`);
  }
  if (options.corpora.some((path) => !path)) throw new TypeError("--corpus 後必須提供路徑");
  if (!options.segmenter) throw new TypeError("--segmenter 後必須提供路徑");
  if (!options.assets) throw new TypeError("--assets 後必須提供路徑");
  return options;
}

function emit(summary) {
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
}

function mapToObject(map) {
  return Object.fromEntries(map);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  if (!options.dictionary) throw new TypeError("缺少 --dictionary <Dict.json>");

  const dictionaryPath = resolve(options.dictionary);
  const statePath = resolve(options.state || `${dictionaryPath}.frequency-state.json`);
  const corpusPaths = (options.corpora.length ? options.corpora : defaultCorpora).map((path) =>
    resolve(path)
  );
  const dictionary = parseDictionary(await readFile(dictionaryPath, "utf8"));
  const normalize = createNormalizer();

  let state;
  try {
    state = parseState(await readFile(statePath, "utf8"));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    if (!options.bootstrapExisting) {
      throw new Error(
        `找不到狀態檔 ${statePath}；首次執行請加 --bootstrap-existing，避免重算既有詞條`
      );
    }
    state = bootstrapState(dictionary, new Date().toISOString(), normalize);
    if (!options.dryRun) await writeJsonAtomically(statePath, state);
    emit({
      status: options.dryRun ? "bootstrap-dry-run" : "bootstrapped",
      statePath,
      entries: Object.keys(dictionary).length,
      canonicalEntries: Object.keys(state.sampled).length
    });
    return;
  }

  const pendingWords = listPendingWords(dictionary, state, normalize);
  if (pendingWords.length < options.threshold && !options.force) {
    emit({
      status: "below-threshold",
      pending: pendingWords.length,
      threshold: options.threshold,
      remaining: options.threshold - pendingWords.length
    });
    return;
  }
  if (pendingWords.length === 0) {
    emit({ status: "up-to-date", pending: 0, threshold: options.threshold });
    return;
  }

  const plan = buildSamplingPlan(dictionary, state, normalize);
  const lockPath = `${statePath}.lock`;
  const lock = await open(lockPath, "wx").catch((error) => {
    if (error?.code === "EEXIST") throw new Error(`已有詞頻批次正在執行：${lockPath}`);
    throw error;
  });
  let temporaryDirectory = null;
  try {
    await lock.writeFile(`${process.pid}\n`, "utf8");
    temporaryDirectory = await mkdtemp(join(dirname(statePath), ".lingxi-frequency-"));
    const userDictionaryPath = join(temporaryDirectory, "provisional-user-dict.txt");
    await writeUserDictionary(userDictionaryPath, plan);

    const counts = new Map(plan.map(({ canonical }) => [canonical, 0]));
    const sources = [];
    for (let index = 0; index < corpusPaths.length; index += 1) {
      const candidatePath = join(temporaryDirectory, `source-${index}-candidates.txt`);
      const segmentedPath = join(temporaryDirectory, `source-${index}-segmented.jsonl`);
      const source = await extractCandidateSentences(
        corpusPaths[index],
        candidatePath,
        counts.keys(),
        normalize
      );
      let accepted = new Map(plan.map(({ canonical }) => [canonical, 0]));
      let outputLines = 0;
      if (source.candidateLines > 0) {
        await runLingxiSegmenter({
          executable: resolve(options.segmenter),
          assets: resolve(options.assets),
          userDictionary: userDictionaryPath,
          input: candidatePath,
          output: segmentedPath
        });
        ({ accepted, outputLines } = await countAcceptedTokens(
          segmentedPath,
          counts.keys(),
          normalize
        ));
      }
      if (outputLines !== source.candidateLines) {
        throw new Error(
          `${source.path} 候選句 ${source.candidateLines} 筆，但分詞器輸出 ${outputLines} 筆`
        );
      }
      for (const [word, frequency] of accepted) {
        counts.set(word, counts.get(word) + frequency);
      }
      sources.push({
        path: source.path,
        bytes: source.bytes,
        modifiedAt: source.modifiedAt,
        lines: source.lines,
        tokens: source.tokens,
        candidateLines: source.candidateLines,
        rawOccurrences: mapToObject(source.rawOccurrences),
        originalTokenOccurrences: mapToObject(source.originalTokenOccurrences),
        acceptedOccurrences: mapToObject(accepted),
        rejectedOccurrences: Object.fromEntries(
          [...source.rawOccurrences].map(([word, raw]) => [word, raw - accepted.get(word)])
        )
      });
    }

    const sampledAt = new Date().toISOString();
    const batchId = `frequency-${sampledAt}`;
    const updated = applySampledFrequencies(dictionary, state, counts, {
      sampledAt,
      batchId,
      normalize
    });
    state.lastBatch = {
      id: batchId,
      sampledAt,
      method: "normalized-candidate-resegmentation",
      words: updated.length,
      zeroFrequencyWords: updated.filter((item) => item.frequency === 0).length,
      provisional: plan,
      sources
    };

    if (!options.dryRun) {
      await writeJsonAtomically(dictionaryPath, dictionary);
      await writeJsonAtomically(statePath, state);
    }
    emit({
      status: options.dryRun ? "dry-run" : "updated",
      method: state.lastBatch.method,
      dictionaryPath,
      statePath,
      pending: pendingWords.length,
      updated: updated.length,
      zeroFrequencyWords: state.lastBatch.zeroFrequencyWords,
      provisional: plan,
      sources,
      frequencies: mapToObject(counts)
    });
  } finally {
    if (temporaryDirectory) await rm(temporaryDirectory, { recursive: true, force: true });
    await lock.close();
    await unlink(lockPath).catch(() => {});
  }
}

main().catch((error) => {
  process.stderr.write(`詞頻批次失敗：${error.message}\n`);
  process.exitCode = 1;
});