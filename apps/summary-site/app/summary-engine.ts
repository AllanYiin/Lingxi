export type SummaryItem = {
  text: string;
  index: number;
  score: number;
  reasons: string[];
};

export type SummaryReport = {
  mode: "extractive" | "structured-preserve";
  summary: string;
  selected: SummaryItem[];
  inputChars: number;
  outputChars: number;
  reductionPercent: number;
};

type BlockKind = "heading" | "prose" | "code" | "technical-context" | "reference";

type DocumentBlock = {
  kind: BlockKind;
  text: string;
  sourceIndex: number;
};

type Candidate = {
  text: string;
  sourceIndex: number;
  vector: Set<string>;
  reasons: string[];
  quality: number;
};

const discourseMarkers = [
  ["研究核心發現", 0.24],
  ["核心發現", 0.22],
  ["研究結論", 0.22],
  ["主要結論", 0.2],
  ["結果顯示", 0.18],
  ["結果指出", 0.18],
  ["結論", 0.16],
  ["建議", 0.14],
  ["因此", 0.12],
] as const;

const stopChars = new Set(
  "，。！？；：、,.!?;:（）()「」『』《》〈〉【】[]的了在與和及是而也就都為將把被之其於又或".split(""),
);
const acronymPattern = /\([A-Z][A-Z0-9&./-]{1,11}\)|（[A-Z][A-Z0-9&./-]{1,11}）/;
const definitionPattern = /(?:是指|意指|指的是|定義為|也就是|全名(?:是|為)|簡稱(?:是|為)|稱為)/;
const predicatePattern = /(?:是|為|有|會|可|能|需|應|支援|提供|顯示|指出|發現|增加|降低|影響|導致|包含|使用|利用|解釋|保留|壓縮|回報|計算|屬於|建議|代表|造成|取決於|不宜|不能|不得|無法|避免)/;
const listPattern = /^(?:[-*+]\s|\d+[.)、]\s*|[一二三四五六七八九十]+[、.)）])/;

function isStructuredMarkdown(text: string): boolean {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const listCount = lines.filter((line) => listPattern.test(line)).length;
  const structured = lines.filter(
    (line) => /^#{1,6}\s/.test(line) || /^(?:[-*+]\s|\d+[.)、]\s*)/.test(line),
  ).length;
  return lines.length >= 4 && listCount >= 3 && structured * 3 >= lines.length * 2;
}

function isReferenceLine(line: string): boolean {
  return /^(?:\[?\d+\]?:?\s*)?(?:https?:\/\/|www\.)\S+/i.test(line.trim());
}

function isCodeLine(line: string): boolean {
  const value = line.trim();
  if (!value) return false;
  if (/^(?:const|let|var|fn|def|class|function|import|export|use|pub|return)\b/.test(value)) {
    return true;
  }
  if (/^(?:if|else|for|while|match|switch)\s*[({]/.test(value)) return true;
  if (/^(?:\$|PS>|>>>?)\s+\S+/.test(value)) return true;
  if (/^(?: {4}|\t)/.test(line) && !listPattern.test(value)) return true;
  const cjkCount = (value.match(/[\u3400-\u9fff]/g) ?? []).length;
  return cjkCount < 8 && /(?:=>|===|!==|::|[{}]\s*;?$|\w+\([^)]*\)\s*;?$)/.test(value);
}

function isTechnicalContextLine(line: string): boolean {
  const value = line.trim();
  if (!value || /^[-*_]{3,}$/.test(value)) return true;
  const cjkCount = (value.match(/[\u3400-\u9fff]/g) ?? []).length;
  const lowerCount = (value.match(/[a-z]/g) ?? []).length;
  const upperCount = (value.match(/[A-Z]/g) ?? []).length;
  const words = value.match(/[A-Za-z][A-Za-z0-9_.:/-]*/g) ?? [];
  const diagramSyntax = /(?:←|→|<-|->|=>|\|\s*[+←→]|\s\+\s)/.test(value);
  const allCapsLabel =
    cjkCount === 0 && upperCount >= Math.max(2, lowerCount * 2) && words.length <= 8;
  return (diagramSyntax && cjkCount < 8) || allCapsLabel;
}

function parseDocumentBlocks(text: string): DocumentBlock[] {
  const blocks: DocumentBlock[] = [];
  let proseLines: string[] = [];
  let codeLines: string[] = [];
  let fenceMarker = "";

  const pushBlock = (kind: BlockKind, value: string) => {
    const normalized = value.trim();
    if (!normalized) return;
    blocks.push({ kind, text: normalized, sourceIndex: blocks.length });
  };
  const flushProse = () => {
    pushBlock("prose", proseLines.join("\n"));
    proseLines = [];
  };
  const flushCode = () => {
    pushBlock("code", codeLines.join("\n"));
    codeLines = [];
  };

  for (const line of text.split(/\r?\n/)) {
    const fence = line.trim().match(/^(```+|~~~+)/)?.[1] ?? "";
    if (fenceMarker) {
      codeLines.push(line);
      if (fence && fence[0] === fenceMarker[0]) {
        flushCode();
        fenceMarker = "";
      }
      continue;
    }
    if (fence) {
      flushProse();
      fenceMarker = fence;
      codeLines.push(line);
      continue;
    }

    const value = line.trim();
    if (!value) {
      flushProse();
      continue;
    }
    if (/^#{1,6}\s+\S/.test(value)) {
      flushProse();
      pushBlock("heading", value);
    } else if (isReferenceLine(value)) {
      flushProse();
      pushBlock("reference", value);
    } else if (isCodeLine(line)) {
      flushProse();
      pushBlock("code", line);
    } else if (isTechnicalContextLine(value)) {
      flushProse();
      pushBlock("technical-context", value);
    } else {
      proseLines.push(line);
    }
  }
  flushProse();
  if (codeLines.length) flushCode();
  return blocks;
}

function terms(text: string): Set<string> {
  const values = new Set<string>();
  for (const match of text.toLowerCase().matchAll(/[a-z][a-z0-9_.:/-]*|[\u3400-\u9fff]+/g)) {
    const value = match[0];
    if (/^[a-z]/.test(value)) {
      if (value.length > 1) values.add(value);
      continue;
    }
    const chars = [...value].filter((char) => !stopChars.has(char));
    for (const char of chars) values.add(char);
    for (let index = 0; index + 1 < chars.length; index += 1) {
      values.add(chars[index] + chars[index + 1]);
    }
  }
  return values;
}

function boundedContextTerms(text: string): Set<string> {
  return new Set([...terms(text)].slice(0, 96));
}

function similarity(left: Set<string>, right: Set<string>): number {
  if (!left.size || !right.size) return 0;
  let shared = 0;
  for (const value of left) if (right.has(value)) shared += 1;
  return shared / Math.sqrt(left.size * right.size);
}

function reasons(text: string): string[] {
  const result: string[] = [];
  if (/\d+(?:\.\d+)?\s*(?:%|％|元|萬元|億元|秒|分鐘|小時|天|週|月|年|kg|GB|MB)/i.test(text)) {
    result.push("關鍵數值");
  }
  if (/(?:19|20)\d{2}[-/.年]/.test(text)) result.push("日期");
  if (/(?:不得|不能|不要|禁止|避免|並非|並未|沒有|不應|無法)/.test(text)) {
    result.push("限制／否定");
  }
  if (acronymPattern.test(text) && definitionPattern.test(text)) result.push("縮略語定義");
  if (listPattern.test(text.trim())) result.push("條列重點");
  if (discourseMarkers.some(([marker]) => text.includes(marker))) result.push("結論訊號");
  return result;
}

function proseQuality(text: string, shortDocument: boolean): number {
  const value = text.trim();
  const cjkCount = (value.match(/[\u3400-\u9fff]/g) ?? []).length;
  const englishWords = value.match(/[A-Za-z][A-Za-z'-]*/g) ?? [];
  const meaningfulCount = (value.match(/[\p{L}\p{N}]/gu) ?? []).length;
  const lowerCount = (value.match(/[a-z]/g) ?? []).length;
  if (meaningfulCount < 4) return 0;
  if (cjkCount === 0 && englishWords.length >= 8 && lowerCount > 0) return 0.85;
  if (listPattern.test(value) && meaningfulCount >= 6) return 0.8;
  if (cjkCount >= 10 && predicatePattern.test(value)) return 1;
  if (cjkCount >= 8 && meaningfulCount >= 12) return 0.85;
  if (shortDocument && cjkCount >= 4 && /[。！？!?]$/.test(value)) return 0.75;
  return 0;
}

function contextForProse(blocks: DocumentBlock[], proseIndex: number): {
  text: string;
  hasTechnicalContext: boolean;
} {
  const parts: string[] = [];
  let hasTechnicalContext = false;

  for (let index = proseIndex - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    if (block.kind === "prose") break;
    if (block.kind === "heading") {
      parts.push(block.text);
      break;
    }
    if (block.kind === "code" || block.kind === "technical-context") {
      parts.push(block.text);
      hasTechnicalContext = true;
    }
  }
  for (let index = proseIndex + 1; index < blocks.length; index += 1) {
    const block = blocks[index];
    if (block.kind === "prose" || block.kind === "heading") break;
    if (block.kind === "code" || block.kind === "technical-context") {
      parts.push(block.text);
      hasTechnicalContext = true;
    }
  }
  return { text: parts.join("\n"), hasTechnicalContext };
}

function emptyReport(inputChars = 0): SummaryReport {
  return {
    mode: "extractive",
    summary: "",
    selected: [],
    inputChars,
    outputChars: 0,
    reductionPercent: inputChars ? 100 : 0,
  };
}

export function summarize(text: string, maxBlocks: number): SummaryReport {
  const normalized = text.trim();
  const inputChars = [...normalized].length;
  if (!normalized) return emptyReport();
  if (isStructuredMarkdown(normalized)) {
    return {
      mode: "structured-preserve",
      summary: normalized,
      selected: [
        { text: normalized, index: 0, score: 1, reasons: ["結構化筆記完整保留"] },
      ],
      inputChars,
      outputChars: inputChars,
      reductionPercent: 0,
    };
  }

  const blocks = parseDocumentBlocks(normalized);
  const proseBlocks = blocks.filter((block) => block.kind === "prose");
  const shortDocument = inputChars < 120 && proseBlocks.length <= 3;
  const candidates: Candidate[] = blocks
    .filter((block) => block.kind === "prose")
    .map((block) => {
      const context = contextForProse(blocks, block.sourceIndex);
      const vector = terms(block.text);
      for (const term of boundedContextTerms(context.text)) vector.add(term);
      const blockReasons = reasons(block.text);
      if (context.hasTechnicalContext) blockReasons.push("程式碼脈絡");
      return {
        text: block.text,
        sourceIndex: block.sourceIndex,
        vector,
        reasons: blockReasons,
        quality: proseQuality(block.text, shortDocument),
      };
    })
    .filter((candidate) => candidate.quality >= 0.55 && candidate.vector.size > 0);

  if (!candidates.length) return emptyReport(inputChars);

  const matrix = candidates.map((_, left) =>
    candidates.map((__, right) =>
      left === right ? 0 : similarity(candidates[left].vector, candidates[right].vector),
    ),
  );
  let scores = candidates.map(() => 1 / candidates.length);
  for (let iteration = 0; iteration < 30; iteration += 1) {
    scores = candidates.map((_, target) => {
      let incoming = 0;
      for (let source = 0; source < candidates.length; source += 1) {
        const total = matrix[source].reduce((sum, value) => sum + value, 0);
        if (total > 0) incoming += (matrix[source][target] / total) * scores[source];
      }
      return 0.15 / candidates.length + 0.85 * incoming;
    });
  }
  const maxScore = Math.max(...scores, 0.0001);
  scores = scores.map((score, index) => {
    const discourseBonus =
      discourseMarkers.find(([marker]) => candidates[index].text.includes(marker))?.[1] ?? 0;
    return Math.min(1, 0.8 * (score / maxScore) + discourseBonus);
  });

  const selected: number[] = [];
  const limit = Math.max(1, Math.min(Math.trunc(maxBlocks) || 1, candidates.length));
  while (selected.length < limit) {
    let bestIndex = -1;
    let bestValue = -1;
    for (let index = 0; index < candidates.length; index += 1) {
      if (selected.includes(index)) continue;
      const overlap = selected.length
        ? Math.max(...selected.map((chosen) => matrix[index][chosen]))
        : 0;
      if (overlap >= 0.84) continue;
      const novelty = 1 - overlap;
      const coverage =
        matrix[index].reduce((sum, value, target) => {
          const previous = selected.length
            ? Math.max(...selected.map((chosen) => matrix[chosen][target]))
            : 0;
          return sum + Math.max(0, value - previous);
        }, 1) / candidates.length;
      const signal = Math.min(1, candidates[index].reasons.length / 3);
      const value =
        (0.52 * scores[index] + 0.23 * coverage + 0.15 * novelty + 0.1 * signal) *
        candidates[index].quality;
      if (value > bestValue) {
        bestValue = value;
        bestIndex = index;
      }
    }
    if (bestIndex < 0) break;
    selected.push(bestIndex);
  }

  const items = selected
    .sort((left, right) => candidates[left].sourceIndex - candidates[right].sourceIndex)
    .map((index) => ({
      text: candidates[index].text,
      index: candidates[index].sourceIndex,
      score: scores[index],
      reasons: candidates[index].reasons,
    }));
  const summary = items.map((item) => item.text).join("\n\n");
  const outputChars = [...summary].length;
  return {
    mode: "extractive",
    summary,
    selected: items,
    inputChars,
    outputChars,
    reductionPercent: inputChars ? Math.max(0, (1 - outputChars / inputChars) * 100) : 0,
  };
}
