import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  parseDictionaryText,
  parseEmotionTaxonomyText,
  previewLegacyEmotionMigration
} from "../core.js";

function parseArgs(argv) {
  const options = {
    dictionary: "",
    output: "",
    pending: "",
    taxonomy: resolve("..", "..", "resources", "affect", "emotion-taxonomy.json")
  };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--dictionary") options.dictionary = argv[++index] ?? "";
    else if (key === "--output") options.output = argv[++index] ?? "";
    else if (key === "--pending") options.pending = argv[++index] ?? "";
    else if (key === "--taxonomy") options.taxonomy = argv[++index] ?? "";
    else if (key === "--help" || key === "-h") {
      process.stdout.write(
        "用法: node batch/migrate-emotions.mjs --dictionary Dict.json --output emotion-lexicon.json [--pending pending.json] [--taxonomy emotion-taxonomy.json]\n"
      );
      process.exit(0);
    } else {
      throw new TypeError(`未知參數：${key}`);
    }
  }
  if (!options.dictionary) throw new TypeError("缺少 --dictionary");
  if (!options.output) throw new TypeError("缺少 --output；工具不會覆寫來源檔");
  if (resolve(options.dictionary) === resolve(options.output)) {
    throw new TypeError("--output 不得與 --dictionary 相同");
  }
  return options;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const dictionary = parseDictionaryText(await readFile(options.dictionary, "utf8"));
  const taxonomy = parseEmotionTaxonomyText(await readFile(options.taxonomy, "utf8"));
  const result = previewLegacyEmotionMigration(dictionary.rows);
  const output = {
    schemaVersion: 1,
    taxonomyVersion: taxonomy.version,
    entries: result.entries
  };
  await writeFile(options.output, `${JSON.stringify(output, null, 2)}\n`, {
    encoding: "utf8",
    flag: "wx"
  });
  if (result.pending.length) {
    const pendingPath = options.pending || `${options.output}.pending.json`;
    await writeFile(pendingPath, `${JSON.stringify(result.pending, null, 2)}\n`, {
      encoding: "utf8",
      flag: "wx"
    });
  }
  process.stdout.write(
    `遷移完成：${result.entries.length} 筆；待人工確認：${result.pending.length} 筆\n`
  );
}

main().catch((error) => {
  process.stderr.write(`情感遷移失敗：${error.message}\n`);
  process.exitCode = 1;
});
