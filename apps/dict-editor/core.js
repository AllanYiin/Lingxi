export const TAG_OPTIONS = [
  ["ag", "形容詞語素"],
  ["a", "形容詞"],
  ["ad", "副形詞"],
  ["an", "名形詞"],
  ["b", "區別詞"],
  ["c", "連接詞"],
  ["dg", "副詞語素"],
  ["d", "副詞"],
  ["e", "感嘆詞"],
  ["f", "方位詞"],
  ["g", "語素"],
  ["h", "前接成分"],
  ["i", "成語"],
  ["j", "簡略詞／縮寫"],
  ["k", "後接成分"],
  ["l", "習用俚語"],
  ["m", "數量詞"],
  ["ng", "名詞語素"],
  ["n", "名詞"],
  ["nr", "人名"],
  ["ns", "地名"],
  ["nt", "機構、團體或品牌"],
  ["nz", "其他專有名詞"],
  ["o", "擬聲詞"],
  ["p", "介詞"],
  ["q", "單位詞"],
  ["r", "代名詞"],
  ["s", "處所詞"],
  ["tg", "時間語素"],
  ["t", "時間詞"],
  ["u", "助詞"],
  ["vg", "動詞語素"],
  ["v", "動詞"],
  ["vd", "副動詞"],
  ["vn", "動名詞"],
  ["w", "標點符號"],
  ["x", "非語素字"],
  ["y", "語氣詞"],
  ["z", "狀態詞"],
  ["hybrid", "中外夾雜"],
  ["unknown", "未知詞"],
  ["unknownnew", "新詞，尚未確認詞性"],
  ["undeterminate", "尚未指派"],
  ["emoji", "表情符號"]
];

export const ENTITY_OPTIONS = [
  ["None", "無"],
  ["ChName", "中文人名"],
  ["JpnName", "日文人名"],
  ["TransName", "翻譯人名"],
  ["Url", "網址"],
  ["Email", "電子郵件"],
  ["Idiom", "成語"],
  ["Slang", "俚語"],
  ["Catchword", "網路流行語"],
  ["Abbreviation", "縮略語"]
];

export const EMOTION_OPTIONS = [
  ["None", "無"],
  ["Happy", "快樂"],
  ["Confidence", "自信"],
  ["Sad", "悲傷"],
  ["Annoyed", "煩悶"],
  ["Unlike", "討厭"],
  ["NoChoice", "無奈"],
  ["Inferior", "自卑"],
  ["Regret", "後悔"],
  ["Blame", "貶責"],
  ["Indifferent", "冷漠"],
  ["Disappointed", "失望"],
  ["Doubt", "懷疑"],
  ["Despise", "輕視"],
  ["Worried", "擔憂"],
  ["Fake", "虛假"],
  ["Fear", "恐懼"],
  ["Angry", "憤怒"],
  ["Dangerous", "危險"]
];

export const DEFAULT_NEW_ENTRY_FREQUENCY = 1;

const ENTITY_CODES = new Set(ENTITY_OPTIONS.map(([code]) => code));
const EMOTION_CODES = new Set(EMOTION_OPTIONS.map(([code]) => code));

function safeInteger(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : fallback;
}

export function decodeDictionaryValue(word, rawValue, id) {
  if (!Array.isArray(rawValue) || rawValue.length < 2) {
    throw new TypeError(`詞條「${word}」必須是至少包含詞性與頻率的陣列`);
  }

  const tag = String(rawValue[0] ?? "unknown");
  const frequency = safeInteger(rawValue[1]);
  let entity = "None";
  let emotion = "None";

  if (rawValue.length === 3 && rawValue[2] != null) {
    const marker = String(rawValue[2]);
    if (ENTITY_CODES.has(marker)) {
      entity = marker;
    } else if (EMOTION_CODES.has(marker)) {
      emotion = marker;
    } else {
      entity = marker;
    }
  } else if (rawValue.length >= 4) {
    entity = rawValue[2] == null ? "None" : String(rawValue[2]);
    emotion = rawValue[3] == null ? "None" : String(rawValue[3]);
  }

  return { id, word, tag, frequency, entity, emotion };
}

export function parseDictionaryText(text) {
  let parsed;
  try {
    parsed = JSON.parse(text.replace(/^\uFEFF/, ""));
  } catch (error) {
    throw new SyntaxError(`JSON 解析失敗：${error.message}`);
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new TypeError("詞典根節點必須是以詞語為 key 的 JSON object");
  }

  const rows = [];
  const warnings = [];
  let id = 1;
  for (const [word, value] of Object.entries(parsed)) {
    try {
      rows.push(decodeDictionaryValue(word, value, id));
      id += 1;
    } catch (error) {
      if (warnings.length < 50) warnings.push(error.message);
    }
  }

  if (rows.length === 0 && Object.keys(parsed).length > 0) {
    throw new TypeError("找不到可用詞條；請確認每個值都是 [詞性, 頻率] 陣列");
  }

  return { rows, warnings };
}

export function encodeDictionaryValue(row) {
  const value = [row.tag || "unknown", safeInteger(row.frequency)];
  const entity = row.entity || "None";
  const emotion = row.emotion || "None";
  if (entity !== "None" && emotion !== "None") {
    value.push(entity, emotion);
  } else if (entity !== "None") {
    value.push(entity);
  } else if (emotion !== "None") {
    value.push(emotion);
  }
  return value;
}

export function serializeDictionaryRows(rows) {
  const lines = rows.map(
    (row) => `  ${JSON.stringify(row.word)}: ${JSON.stringify(encodeDictionaryValue(row))}`
  );
  return `{\n${lines.join(",\n")}\n}\n`;
}

export function filterAndSortRows(rows, filters = {}, sort = {}) {
  const rawQuery = String(filters.query ?? "").trim();
  const foldLatinCase = /[A-Za-z]/.test(rawQuery);
  const query = foldLatinCase ? rawQuery.toLocaleLowerCase("zh-Hant") : rawQuery;
  const minFrequency =
    filters.minFrequency === "" || filters.minFrequency == null
      ? null
      : safeInteger(filters.minFrequency);
  const maxFrequency =
    filters.maxFrequency === "" || filters.maxFrequency == null
      ? null
      : safeInteger(filters.maxFrequency);

  const filtered = rows.filter((row) => {
    const searchableWord = foldLatinCase ? row.word.toLocaleLowerCase("zh-Hant") : row.word;
    if (query && !searchableWord.includes(query)) return false;
    if (filters.tag && row.tag !== filters.tag) return false;
    if (filters.entity && row.entity !== filters.entity) return false;
    if (filters.emotion && row.emotion !== filters.emotion) return false;
    if (minFrequency != null && row.frequency < minFrequency) return false;
    if (maxFrequency != null && row.frequency > maxFrequency) return false;
    return true;
  });

  const key = sort.key || "source";
  if (key === "source") return filtered;
  const direction = sort.direction === "desc" ? -1 : 1;
  return filtered.sort((a, b) => {
    if (key === "frequency") return direction * (a.frequency - b.frequency);
    return (
      direction *
      String(a[key] ?? "").localeCompare(String(b[key] ?? ""), "zh-Hant", {
        numeric: true,
        sensitivity: "base"
      })
    );
  });
}

export function previewDictionaryReplacement(rows, before, after) {
  if (!before) throw new TypeError("「尋找內容」不可為空白");
  const matched = rows.filter((row) => row.word.includes(before));
  const movingWords = new Set(matched.map((row) => row.word));
  const occupied = new Set(rows.filter((row) => !movingWords.has(row.word)).map((row) => row.word));
  const targets = new Set();
  const changes = [];
  const conflicts = [];

  for (const row of matched) {
    const nextWord = row.word.split(before).join(after);
    if (!nextWord) {
      conflicts.push({ row, nextWord, reason: "代換後詞語為空白" });
    } else if (occupied.has(nextWord) || targets.has(nextWord)) {
      conflicts.push({ row, nextWord, reason: "代換後會與既有詞條重複" });
    } else {
      targets.add(nextWord);
      changes.push({ row, nextWord });
    }
  }

  return { matchedCount: matched.length, changes, conflicts };
}

export function applyDictionaryReplacement(preview) {
  for (const change of preview.changes) change.row.word = change.nextWord;
  return preview.changes.length;
}

export function parseTrainingText(text) {
  return text.replace(/^\uFEFF/, "").split(/\r?\n/);
}

export function inferPotentialWord(replacement) {
  return (
    String(replacement)
      .split("|")
      .map((part) => part.trim())
      .filter((part) => [...part].length >= 2)
      .sort((a, b) => [...b].length - [...a].length)[0] ?? ""
  );
}

export function replaceTrainingLines(lines, before, after) {
  if (!before) throw new TypeError("「尋找內容」不可為空白");
  let changedCount = 0;
  const nextLines = lines.map((line) => {
    if (!line.includes(before)) return line;
    changedCount += 1;
    return line
      .split(before)
      .join(after)
      .replace(/\|{2,}/g, "|")
      .replace(/\|+$/g, "");
  });
  return { lines: nextLines, changedCount };
}

export function deleteFirstExactTrainingLine(lines, target) {
  const index = lines.indexOf(target);
  if (index < 0) return { lines, deleted: false };
  return { lines: [...lines.slice(0, index), ...lines.slice(index + 1)], deleted: true };
}


// v1 結構化自訂辭典與情感詞典 -------------------------------------------------

export const LEGACY_EMOTION_MIGRATION = Object.freeze({
  Happy: { emotions: ["joy.joy"] },
  Confidence: { emotions: ["outlook.confidence"] },
  Sad: { emotions: ["sadness.sadness"] },
  Annoyed: { emotions: ["anger.annoyance"] },
  Unlike: { emotions: ["aversion.dislike"] },
  NoChoice: { emotions: ["powerlessness.helplessness"] },
  Inferior: { emotions: ["self_conscious.inferiority"] },
  Regret: { emotions: ["self_conscious.regret"] },
  Blame: { appraisals: ["blame"] },
  Indifferent: { emotions: ["detachment.indifference"] },
  Disappointed: { emotions: ["sadness.disappointment"] },
  Doubt: { emotions: ["cognition.doubt"] },
  Despise: { emotions: ["aversion.contempt"] },
  Worried: { emotions: ["fear.worry"] },
  Fake: { semanticFlags: ["deception"] },
  Fear: { emotions: ["fear.fear"] },
  Angry: { emotions: ["anger.anger"] },
  Dangerous: { semanticFlags: ["threat"] }
});

function parseJsonObject(text, label) {
  let value;
  try {
    value = JSON.parse(text.replace(/^\uFEFF/, ""));
  } catch (error) {
    throw new SyntaxError(`${label} JSON 解析失敗：${error.message}`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label}根節點必須是 JSON object`);
  }
  return value;
}

export function parseCustomLexiconText(text) {
  const value = parseJsonObject(text, "自訂辭典");
  const allowedRoot = new Set(["schemaVersion", "id", "domain", "priority", "enabled", "entries"]);
  for (const key of Object.keys(value)) {
    if (!allowedRoot.has(key)) throw new TypeError(`自訂辭典含未知欄位「${key}」`);
  }
  if (value.schemaVersion !== 1) throw new TypeError("自訂辭典 schemaVersion 必須為 1");
  if (typeof value.id !== "string" || !value.id.trim()) throw new TypeError("自訂辭典 id 不可為空");
  if (typeof value.domain !== "string" || !value.domain.trim()) {
    throw new TypeError("自訂辭典 domain 不可為空");
  }
  const priority = value.priority ?? 0;
  if (!Number.isInteger(priority) || priority < -10 || priority > 10) {
    throw new TypeError("自訂辭典 priority 必須是 -10 到 10 的整數");
  }
  if (!Array.isArray(value.entries)) throw new TypeError("自訂辭典 entries 必須是陣列");
  const entries = value.entries.map((entry, index) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new TypeError(`第 ${index + 1} 筆自訂詞條必須是 object`);
    }
    const allowed = new Set(["word", "pos", "affect"]);
    for (const key of Object.keys(entry)) {
      if (!allowed.has(key)) throw new TypeError(`自訂詞條含未知欄位「${key}」`);
    }
    if (typeof entry.word !== "string" || [...entry.word].length < 2 || [...entry.word].length > 255) {
      throw new TypeError(`第 ${index + 1} 筆自訂詞必須包含 2 至 255 個字元`);
    }
    return {
      word: entry.word,
      ...(entry.pos ? { pos: String(entry.pos) } : {}),
      ...(entry.affect ? { affect: structuredClone(entry.affect) } : {})
    };
  });
  return {
    schemaVersion: 1,
    id: value.id.trim(),
    domain: value.domain.trim(),
    priority,
    enabled: value.enabled !== false,
    entries
  };
}

export function serializeCustomLexicon(value) {
  return `${JSON.stringify(parseCustomLexiconText(JSON.stringify(value)), null, 2)}\n`;
}

export function parseEmotionTaxonomyText(text) {
  const value = parseJsonObject(text, "情感 taxonomy");
  if (value.schemaVersion !== 1 || typeof value.version !== "string" || !value.version) {
    throw new TypeError("情感 taxonomy schemaVersion/version 無效");
  }
  if (!Array.isArray(value.labels)) throw new TypeError("情感 taxonomy labels 必須是陣列");
  const ids = new Set();
  const families = new Set([
    "joy", "affection", "esteem", "outlook", "sadness", "fear", "anger", "aversion",
    "self_conscious", "powerlessness", "social_comparison", "surprise", "cognition", "detachment"
  ]);
  const defaultPolarities = new Set(["positive", "negative", "neutral", "contextual"]);
  for (const label of value.labels) {
    if (!label?.id || !label?.family || !label?.nameZhTw) {
      throw new TypeError("每個情感標籤都必須有 id、family 與 nameZhTw");
    }
    if (!families.has(label.family)) throw new TypeError(`情感標籤「${label.id}」使用未知家族`);
    if (!defaultPolarities.has(label.defaultPolarity)) {
      throw new TypeError(`情感標籤「${label.id}」的 defaultPolarity 無效`);
    }
    if (ids.has(label.id)) throw new TypeError(`情感標籤 id「${label.id}」重複`);
    ids.add(label.id);
  }
  return value;
}

export function derivePolarity(emotions, taxonomy, override = null, contextDependent = false) {
  if (override) return override;
  const labels = new Map(taxonomy.labels.map((label) => [label.id, label]));
  let positive = false;
  let negative = false;
  let contextual = contextDependent;
  for (const id of emotions) {
    const label = labels.get(id);
    if (!label) throw new TypeError(`未知情感標籤「${id}」`);
    if (label.enabled === false) throw new TypeError(`情感標籤「${id}」已棄用`);
    if (label.defaultPolarity === "positive") positive = true;
    if (label.defaultPolarity === "negative") negative = true;
    if (label.defaultPolarity === "mixed") positive = negative = true;
    if (label.defaultPolarity === "contextual") contextual = true;
  }
  if (positive && negative) return "mixed";
  if (contextual) return "contextual";
  if (positive) return "positive";
  if (negative) return "negative";
  return "neutral";
}

export function parseAffectLexiconText(text, taxonomy) {
  const value = parseJsonObject(text, "情感詞典");
  if (value.schemaVersion !== 1) throw new TypeError("情感詞典 schemaVersion 必須為 1");
  if (value.taxonomyVersion !== taxonomy.version) throw new TypeError("情感詞典 taxonomyVersion 不一致");
  if (!Array.isArray(value.entries)) throw new TypeError("情感詞典 entries 必須是陣列");
  const words = new Set();
  const entries = value.entries.map((entry, index) => {
    if (!entry?.word || !Array.isArray(entry.emotions ?? [])) {
      throw new TypeError(`第 ${index + 1} 筆情感詞條格式無效`);
    }
    if (words.has(entry.word)) throw new TypeError(`情感詞「${entry.word}」重複`);
    words.add(entry.word);
    const emotions = [...new Set(entry.emotions ?? [])];
    if (entry.polarity && !["mixed", "contextual"].includes(entry.polarity)) {
      throw new TypeError(`情感詞「${entry.word}」的 polarity 只能覆寫為 mixed 或 contextual`);
    }
    if (emotions.length !== (entry.emotions ?? []).length) {
      throw new TypeError(`情感詞「${entry.word}」含重複標籤`);
    }
    derivePolarity(emotions, taxonomy, entry.polarity, entry.contextDependent);
    return structuredClone(entry);
  });
  return { schemaVersion: 1, taxonomyVersion: taxonomy.version, entries };
}

export function serializeAffectLexicon(value, taxonomy) {
  return `${JSON.stringify(parseAffectLexiconText(JSON.stringify(value), taxonomy), null, 2)}\n`;
}

export function previewLegacyEmotionMigration(rows) {
  const entries = [];
  const pending = [];
  for (const row of rows) {
    if (!row.emotion || row.emotion === "None") continue;
    const mapping = LEGACY_EMOTION_MIGRATION[row.emotion];
    if (!mapping) {
      pending.push({ word: row.word, legacyCode: row.emotion });
      continue;
    }
    entries.push({
      word: row.word,
      ...structuredClone(mapping),
      source: "legacy-dict-migration"
    });
  }
  return { entries, pending };
}
