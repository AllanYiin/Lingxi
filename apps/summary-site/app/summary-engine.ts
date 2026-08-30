import type { PosToken } from "./lingxi/loader";

export type BlockKind =
  | "paragraph" | "heading" | "fenced-code" | "indented-code"
  | "ordered-list-item" | "unordered-list-item" | "blockquote"
  | "table" | "html" | "thematic-break";

export type Decision = "preserve_exact" | "select_exact" | "summarize_within" | "compact_pos" | "context_only" | "omit";

export type SignalSpan = { kind: string; text: string; byteStart: number; byteEnd: number };
export type SummarySignals = {
  properNounCount: number; modelProperNounCount: number; negationCount: number;
  emphasisCount: number; listItem: boolean; objectNameCount: number; dateCount: number;
  numberCount: number; quantityCount: number; moneyCount: number; acronymCount: number;
  spans: SignalSpan[];
};
export type SummaryScore = {
  relevance: number; coverageGain: number; novelty: number; signal: number; finalScore: number;
};
export type SummaryBlock = {
  index: number; kind: BlockKind; byteStart: number; byteEnd: number; depth: number;
  decision: Decision; sourceText: string; outputText: string;
  selectedSpans: Array<{ text: string; byteStart: number; byteEnd: number; forcedByNegation: boolean }>;
  removedTokens: Array<{ word: string; tag: string; start: number; end: number }>;
  signals: SummarySignals; score: SummaryScore | null; children: SummaryBlock[];
};
export type SummaryDocument = {
  schemaVersion: 2; mode: "hierarchical-extractive"; text: string; blocks: SummaryBlock[];
  budget: { requestedMaxBlocks: number; selectedRankedBlocks: number; preservedBlocks: number;
    forcedNegationClauses: number; actualOutputBlocks: number; overflowReasons: string[] };
  inputChars: number; outputChars: number; reductionPercent: number;
};

type Parsed = { kind: BlockKind; start: number; end: number; byteStart: number; byteEnd: number; depth: number };
type Candidate = { blockIndex: number; terms: Map<string, number>; length: number };
const encoder = new TextEncoder();

const numberRe = /[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)/g;
const dateRe = /(?:民國\s*)?\d{2,4}年(?:\d{1,2}月(?:\d{1,2}日)?)?|\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\d{1,2}月\d{1,2}日|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s*)?\d{4}\b/gi;
const moneyRe = /(?:(?:NT|US)?[$€£¥￥]\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)|(?:TWD|NTD|USD|EUR|JPY|CNY|RMB)\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)|[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:元|萬元|億元|兆元|美元|美金|歐元|日圓|人民幣|TWD|NTD|USD|EUR|JPY|CNY|RMB))/gi;
const quantityRe = /[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:%|％|bps|ms|毫秒|秒|分鐘|小時|天|週|周|月|季|年|公斤|公克|克|kg|g|公里|公尺|公分|毫米|km|m|cm|mm|公升|毫升|l|ml|kb|mb|gb|tb|hz|khz|mhz|ghz|w|kw|mw|v|kv|a|ma|°c|℃|°f)/gi;
const acronymRe = /\b[A-Z][A-Z0-9&./-]{1,11}\b/g;
const objectRe = /`[^`\r\n]+`|(?:[A-Za-z][A-Za-z0-9_-]*\.)+[A-Za-z_][A-Za-z0-9_-]*|\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b|\b[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+\b/g;
const englishProperRe = /\b(?:[A-Z][a-z]+(?:\s+(?:of|the|and|&|[A-Z][a-z]+)){1,5}|[A-Z][A-Z0-9&./-]{1,11})\b/g;
const cjkProperRe = /[\u3400-\u9fff]{2,20}(?:公司|集團|大學|學院|政府|委員會|基金會|協會|銀行|醫院|研究院|法院|市|縣|國)|[「『“"]([^」』”"\r\n]{2,30})[」』”"]/g;
const emphasisRe = /\*\*[^*\r\n]+\*\*|（[^）\r\n]+）|\([^\r\n)]+\)/g;
const englishNegationRe = /\b(?:not|no|never|without|cannot|can't|won't|neither|nor|prohibit(?:ed|s|ing)?|forbid(?:den|s|ding)?|avoid(?:ed|s|ing)?)\b/gi;

function bytes(value: string): number { return encoder.encode(value).length; }
function byteAt(text: string, unit: number): number { return bytes(text.slice(0, unit)); }
function lineRanges(text: string): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let start = 0;
  for (let i = 0; i < text.length; i += 1) if (text[i] === "\n") { out.push([start, i + 1]); start = i + 1; }
  if (start < text.length) out.push([start, text.length]);
  return out;
}
function noEol(value: string): string { return value.replace(/\r?\n$/, ""); }
function indent(line: string): number { return line.match(/^[ \t]*/)?.[0].length ?? 0; }
function fence(line: string): [string, number] | null {
  const lead = line.match(/^ */)?.[0].length ?? 0;
  if (lead > 3) return null;
  const match = line.slice(lead).match(/^(`{3,}|~{3,})/);
  return match ? [match[1][0], match[1].length] : null;
}
function listInfo(line: string): { ordered: boolean; indent: number; markerEnd: number } | null {
  const lead = indent(line); const value = line.slice(lead);
  const bullet = value.match(/^(?:[-*+] |[•‧▪◦] )/);
  if (bullet) return { ordered: false, indent: lead, markerEnd: lead + bullet[0].length };
  const ordered = value.match(/^(?:\d+|[一二三四五六七八九十]+)(?:[.)）] |、)/);
  return ordered ? { ordered: true, indent: lead, markerEnd: lead + ordered[0].length } : null;
}
function thematic(line: string): boolean {
  const v = line.replace(/\s/g, ""); return v.length >= 3 && (/^-+$/.test(v) || /^\*+$/.test(v) || /^_+$/.test(v));
}
function htmlStart(line: string): boolean {
  return /^\s*(?:<!--|<!DOCTYPE|<\?|<\/?(?:address|article|aside|blockquote|body|div|dl|fieldset|figure|footer|form|h[1-6]|head|header|html|iframe|main|nav|ol|p|section|summary|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s|>|\/))/i.test(line);
}
function tableDelimiter(line: string): boolean {
  const value = line.trim().replace(/^\||\|$/g, "");
  return Boolean(value) && value.split("|").every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}
function structural(line: string): boolean {
  const v = line.trimStart();
  return Boolean(fence(line)) || line.startsWith("    ") || line.startsWith("\t")
    || /^#{1,6}\s/.test(v) || thematic(line) || Boolean(listInfo(line)) || v.startsWith(">") || htmlStart(line);
}
function parsed(kind: BlockKind, start: number, end: number, depth: number, text: string): Parsed {
  return { kind, start, end, byteStart: byteAt(text, start), byteEnd: byteAt(text, end), depth };
}

function parseBlocks(text: string): Parsed[] {
  const lines = lineRanges(text); const out: Parsed[] = []; let i = 0;
  while (i < lines.length) {
    const [start, end] = lines[i]; const line = noEol(text.slice(start, end));
    if (!line.trim()) { i += 1; continue; }
    const open = fence(line);
    if (open) {
      let j = i + 1;
      while (j < lines.length) { const v = noEol(text.slice(...lines[j])); j += 1; if (new RegExp(`^\\s*\\${open[0]}{${open[1]},}\\s*$`).test(v)) break; }
      out.push(parsed("fenced-code", start, lines[j - 1][1], 0, text)); i = j; continue;
    }
    if (line.startsWith("    ") || line.startsWith("\t")) {
      let j = i + 1; while (j < lines.length && (!noEol(text.slice(...lines[j])).trim() || /^(?: {4}|\t)/.test(noEol(text.slice(...lines[j]))))) j += 1;
      out.push(parsed("indented-code", start, lines[j - 1][1], 0, text)); i = j; continue;
    }
    if (/^\s*#{1,6}\s/.test(line)) { out.push(parsed("heading", start, end, line.trimStart().match(/^#+/)?.[0].length ?? 1, text)); i += 1; continue; }
    if (i + 1 < lines.length) {
      const next = noEol(text.slice(...lines[i + 1]));
      if (/^\s*(?:={3,}|-{3,})\s*$/.test(next)) { out.push(parsed("heading", start, lines[i + 1][1], next.trim().startsWith("=") ? 1 : 2, text)); i += 2; continue; }
      if (line.includes("|") && tableDelimiter(next)) {
        let j = i + 2; while (j < lines.length && noEol(text.slice(...lines[j])).trim() && noEol(text.slice(...lines[j])).includes("|")) j += 1;
        out.push(parsed("table", start, lines[j - 1][1], 0, text)); i = j; continue;
      }
    }
    if (thematic(line)) { out.push(parsed("thematic-break", start, end, 0, text)); i += 1; continue; }
    const list = listInfo(line);
    if (list) {
      let j = i + 1;
      while (j < lines.length) {
        const value = noEol(text.slice(...lines[j]));
        if (!value.trim()) { j += 1; continue; }
        const nested = listInfo(value); if (nested) break;
        if (indent(value) > list.indent) { j += 1; continue; }
        break;
      }
      out.push(parsed(list.ordered ? "ordered-list-item" : "unordered-list-item", start, lines[j - 1][1], Math.floor(list.indent / 2), text)); i = j; continue;
    }
    if (line.trimStart().startsWith(">")) {
      let j = i + 1; while (j < lines.length && (noEol(text.slice(...lines[j])).trimStart().startsWith(">") || !noEol(text.slice(...lines[j])).trim())) j += 1;
      out.push(parsed("blockquote", start, lines[j - 1][1], 0, text)); i = j; continue;
    }
    if (htmlStart(line)) {
      let j = i + 1; while (j < lines.length && noEol(text.slice(...lines[j])).trim()) j += 1;
      out.push(parsed("html", start, lines[j - 1][1], 0, text)); i = j; continue;
    }
    let j = i + 1;
    while (j < lines.length && noEol(text.slice(...lines[j])).trim() && !structural(noEol(text.slice(...lines[j])))) j += 1;
    out.push(parsed("paragraph", start, lines[j - 1][1], 0, text)); i = j;
  }
  return out;
}

function matches(text: string, kind: string, regex: RegExp, base: number): SignalSpan[] {
  regex.lastIndex = 0; const out: SignalSpan[] = [];
  for (const match of text.matchAll(regex)) {
    const start = match.index ?? 0; out.push({ kind, text: match[0], byteStart: base + byteAt(text, start), byteEnd: base + byteAt(text, start + match[0].length) });
  }
  return out;
}
function tokensInRange(tokens: PosToken[], start: number, end: number): PosToken[] {
  return tokens.filter((token) => token.start >= start && token.end <= end);
}

function isModelProperNoun(tag: string): boolean {
  return tag.startsWith("Nb") || tag.startsWith("Nc");
}

function signals(text: string, base: number, listItem: boolean, posTokens: PosToken[] = [], baseUnit = 0): SummarySignals {
  const localTokens = tokensInRange(posTokens, baseUnit, baseUnit + text.length);
  const modelProper = localTokens.filter((token) => isModelProperNoun(token.tag));
  const posSpans: SignalSpan[] = localTokens.flatMap((token) => {
    const localStart = token.start - baseUnit;
    const localEnd = token.end - baseUnit;
    const span = (kind: string): SignalSpan => ({
      kind,
      text: token.word,
      byteStart: base + byteAt(text, localStart),
      byteEnd: base + byteAt(text, localEnd),
    });
    if (isModelProperNoun(token.tag)) return [span("proper_noun")];
    if (token.tag.startsWith("Nd")) return [span("date")];
    if (token.tag.startsWith("Neu")) return [span("number")];
    if (token.tag.startsWith("Neq")) return [span("quantity")];
    return [];
  });
  let spans = [
    ...matches(text, "money", moneyRe, base), ...matches(text, "date", dateRe, base),
    ...matches(text, "quantity", quantityRe, base), ...matches(text, "number", numberRe, base),
    ...matches(text, "acronym", acronymRe, base), ...matches(text, "object_name", objectRe, base),
    ...matches(text, "proper_noun", englishProperRe, base), ...matches(text, "proper_noun", cjkProperRe, base),
    ...matches(text, "emphasis", emphasisRe, base),
    ...posSpans,
  ];
  const protectedNumeric = spans.filter((s) => s.kind === "date" || s.kind === "money");
  spans = spans.filter((s) => s.kind !== "quantity" || !protectedNumeric.some((p) => s.byteStart >= p.byteStart && s.byteEnd <= p.byteEnd));
  const exclusions = ["不只", "不僅", "不但", "非常", "是否", "未來", "無論", "否則"];
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]; if (!"不未無沒非否勿莫".includes(ch)) continue;
    if (exclusions.some((v) => text.slice(i).startsWith(v)) || (ch === "否" && text[i - 1] === "是")) continue;
    spans.push({ kind: "negation", text: ch, byteStart: base + byteAt(text, i), byteEnd: base + byteAt(text, i + 1) });
  }
  englishNegationRe.lastIndex = 0;
  for (const match of text.matchAll(englishNegationRe)) {
    const i = match.index ?? 0; const lower = text.toLowerCase();
    if (lower.slice(i).startsWith("not only") || (match[0].toLowerCase() === "not" && lower.slice(0, i).endsWith("whether or ")) || (match[0].toLowerCase() === "nor" && lower.slice(0, i).endsWith("neither "))) continue;
    spans.push({ kind: "negation", text: match[0], byteStart: base + byteAt(text, i), byteEnd: base + byteAt(text, i + match[0].length) });
  }
  if (listItem) spans.push({ kind: "list_item", text: listInfo(text)?.markerEnd ? text.slice(0, listInfo(text)!.markerEnd) : "", byteStart: base, byteEnd: base + bytes(text.slice(0, listInfo(text)?.markerEnd ?? 0)) });
  spans.sort((a, b) => a.byteStart - b.byteStart || a.byteEnd - b.byteEnd || a.kind.localeCompare(b.kind));
  spans = spans.filter((s, i) => !i || s.kind !== spans[i - 1].kind || s.byteStart !== spans[i - 1].byteStart || s.byteEnd !== spans[i - 1].byteEnd);
  const count = (kind: string) => spans.filter((s) => s.kind === kind).length;
  return { properNounCount: count("proper_noun"), modelProperNounCount: modelProper.length, negationCount: count("negation"), emphasisCount: count("emphasis"), listItem,
    objectNameCount: count("object_name"), dateCount: count("date"), numberCount: count("number"), quantityCount: count("quantity"), moneyCount: count("money"), acronymCount: count("acronym"), spans };
}
function signalScore(s: SummarySignals): number {
  const p = (v: number) => v > 0 ? 1 : 0;
  return Math.min(1, .55*p(s.modelProperNounCount)+.35*p(s.moneyCount)+.30*p(s.dateCount)+.25*p(s.quantityCount)+.20*p(s.numberCount)+.10*p(s.acronymCount)+.05*p(s.objectNameCount)+.05*p(s.properNounCount));
}
function termMap(text: string): Map<string, number> {
  const out = new Map<string, number>(); const add = (v: string) => out.set(v, (out.get(v) ?? 0) + 1);
  for (const match of text.toLowerCase().matchAll(/[a-z0-9_.-]+|[\u3400-\u9fff]+/g)) {
    const v = match[0]; if (/^[a-z0-9]/.test(v)) add(v); else { const chars = [...v]; chars.forEach(add); for (let i=0;i+1<chars.length;i+=1) add(chars[i]+chars[i+1]); }
  }
  return out;
}
function bm25(a:Candidate,b:Candidate,df:Map<string,number>,count:number,avg:number):number {const directed=(query:Candidate,doc:Candidate)=>{let score=0;for(const term of query.terms.keys()){const frequency=doc.terms.get(term);if(!frequency)continue;const documentFrequency=df.get(term)??1;const idf=Math.log((count-documentFrequency+.5)/(documentFrequency+.5)+1);const norm=frequency+1.2*(1-.75+.75*doc.length/Math.max(1,avg));score+=idf*frequency*2.2/norm;}return score/Math.max(1,query.terms.size);};const value=(directed(a,b)+directed(b,a))/2;return value/(1+value);}
function discourse(text: string): number {
  const lower=text.toLowerCase();
  if (["研究核心發現","核心發現","研究結論","主要結論","是指","定義為","key finding","main finding","research conclusion","main conclusion","is defined as","refers to"].some((m)=>lower.includes(m))) return 1;
  if (["結果顯示","結果指出","結論","建議","因此","所以","results show","results indicate","conclusion","recommend","therefore"].some((m)=>lower.includes(m))) return .75;
  return .25;
}
function sentenceParts(text: string): Array<{text:string;start:number;end:number}> {
  const out=[] as Array<{text:string;start:number;end:number}>;
  const push=(start:number,end:number)=>{const raw=text.slice(start,end);const value=raw.trim();if(value){const local=start+raw.indexOf(value);out.push({text:value,start:local,end:local+value.length});}};
  const terminal=(character:string,index:number)=>"。！？!?…".includes(character)||(character==="."&&!(/\d/.test(text[index-1]??"")&&/\d/.test(text[index+1]??"")));
  const closer=(character:string)=>"\"'”’」』》〉）)】]〕".includes(character);
  let start=0,pending=false;
  for(let index=0;index<text.length;index+=1){const character=text[index];
    if(character==="\n"||character==="\r"){const candidate=text.slice(start,index);const boundary=candidate.trimEnd().at(-1)??"";if(requiresFollowingClause(candidate,boundary))continue;push(start,index);start=index+1;pending=false;continue;}
    if(pending&&!terminal(character,index)&&!closer(character)){push(start,index);start=index;pending=false;}
    if(terminal(character,index))pending=true;
  }
  push(start,text.length);
  return out;
}

function requiresFollowingClause(candidate: string, boundary: string): boolean {
  if (boundary !== "，" && boundary !== ",") return false;
  const content = candidate.trim().replace(/[，,]$/, "").replace(/^[\d.)）、]+/, "").trim();
  const dependentPrefix = ["如果", "若", "倘若", "只要", "除非", "一旦", "當", "雖然", "儘管", "即使", "由於", "因為", "除了"];
  const quantitativeCondition = ["超過", "高於", "低於", "少於", "未滿", "達到", "多於"].some((marker) => content.includes(marker))
    && ["毫秒", "秒", "分鐘", "小時", "天", "日", "週", "周", "月", "季", "年", "%", "％", "元", "萬元", "億元", "公里", "公尺", "公分", "公斤", "GB", "MB", "TB"].some((unit) => content.endsWith(unit));
  const definition = /[（(][A-Z][A-Z0-9&./-]{1,11}[）)]/.test(content)
    || ["是指", "意指", "指的是", "定義為", "也就是", "換言之"].some((marker) => content.includes(marker));
  const orderedSequence = /先(?!生|進|前|祖)/.test(content);
  return ["不只", "不僅", "不但"].some((marker) => content.includes(marker))
    || dependentPrefix.some((prefix) => content.startsWith(prefix))
    || quantitativeCondition
    || definition
    || orderedSequence;
}

function clauseParts(text: string): ReturnType<typeof sentenceParts> {
  const out: ReturnType<typeof sentenceParts> = [];
  const closer = new Map<string, string>([["(", ")"], ["（", "）"], ["[", "]"], ["【", "】"], ["〔", "〕"], ["{", "}"], ["「", "」"], ["『", "』"], ["《", "》"], ["〈", "〉"], ["“", "”"], ["‘", "’"]]);
  for (const sentence of sentenceParts(text)) {
    let start = sentence.start;
    const stack: string[] = [];
    let markdownBold = false;
    let inlineCode = false;
    for (let index = sentence.start; index < sentence.end; index += 1) {
      const character = text[index];
      if (character === "*" && text[index + 1] === "*" && !inlineCode) { markdownBold = !markdownBold; index += 1; continue; }
      if (character === "`" && !markdownBold) { inlineCode = !inlineCode; continue; }
      if (markdownBold || inlineCode) continue;
      if (closer.has(character)) stack.push(closer.get(character)!);
      else if (stack.at(-1) === character) stack.pop();
      else if (character === "\"" || character === "'") {
        if (stack.at(-1) === character) stack.pop(); else stack.push(character);
      }
      const boundary = character === "，" || character === ",";
      const numericComma = (character === "," || character === "，") && /\d/.test(text[index - 1] ?? "") && /\d/.test(text[index + 1] ?? "");
      if (!stack.length && boundary && !numericComma) {
        const end = index + 1;
        if (!requiresFollowingClause(text.slice(start, end), character)) {
          const raw = text.slice(start, end);
          const value = raw.trim();
          if (value) { const local = start + raw.indexOf(value); out.push({ text: value, start: local, end: local + value.length }); }
          start = end;
        }
      }
    }
    const raw = text.slice(start, sentence.end);
    const value = raw.trim();
    if (value) { const local = start + raw.indexOf(value); out.push({ text: value, start: local, end: local + value.length }); }
  }
  return out;
}

function isShortNominalPart(part: ReturnType<typeof sentenceParts>[number], posTokens: PosToken[], baseUnit: number): boolean {
  const body = part.text.replace(/[，,。！？!?…\s]+$/g, "");
  if ([...body].length > 8) return false;
  const lexical = tokensInRange(posTokens, baseUnit + part.start, baseUnit + part.end)
    .filter((token) => token.tag !== "PUNCTUATIONCATEGORY");
  if (!lexical.length) return /^[\p{L}\p{N}&./+-]+$/u.test(body);
  return lexical.every((token) => /^(?:N|FW|Neu|Neq)/.test(token.tag));
}

function mergeEnumerationParts(text: string, parts: ReturnType<typeof sentenceParts>, posTokens: PosToken[], baseUnit: number): ReturnType<typeof sentenceParts> {
  const ranges: Array<{ start: number; end: number }> = [];
  for (let index = 0; index < parts.length;) {
    if (!isShortNominalPart(parts[index], posTokens, baseUnit)) { index += 1; continue; }
    let end = index + 1;
    while (end < parts.length && isShortNominalPart(parts[end], posTokens, baseUnit)) end += 1;
    if (end - index >= 2) ranges.push({ start: Math.max(0, index - 1), end });
    index = end;
  }
  if (!ranges.length) return parts;
  const mergedRanges = ranges.reduce<Array<{ start: number; end: number }>>((out, range) => {
    const previous = out.at(-1);
    if (previous && range.start <= previous.end) previous.end = Math.max(previous.end, range.end);
    else out.push({ ...range });
    return out;
  }, []);
  const out: ReturnType<typeof sentenceParts> = [];
  let index = 0;
  for (const range of mergedRanges) {
    while (index < range.start) out.push(parts[index++]);
    const start = parts[range.start].start;
    const end = parts[range.end - 1].end;
    out.push({ text: text.slice(start, end), start, end });
    index = range.end;
  }
  while (index < parts.length) out.push(parts[index++]);
  return out;
}

const semanticFunctionWords = new Set([
  "不", "未", "無", "沒", "非", "否", "勿", "莫", "最", "至少", "至多", "約", "近", "僅", "只",
  "仍", "仍然", "已", "已經", "將", "曾", "可能", "必須", "應", "應該", "如果", "若", "除非", "即使",
  "雖然", "但是", "但", "因為", "所以", "因此", "否則", "不但", "不僅",
]);
const interjectionWords = new Set(["啊", "呀", "喔", "哦", "唉", "哎", "哇", "欸", "誒"]);

function removableByPos(token: PosToken): boolean {
  if (semanticFunctionWords.has(token.word)) return false;
  if (/^(?:D|Da|Dfa|Dfb|Di|Dk)$/.test(token.tag)) return true;
  if (/^C(?:aa|ab|ba|bb)$/.test(token.tag)) return true;
  return token.tag === "I" || (token.tag === "T" && interjectionWords.has(token.word));
}

function compactByPos(text: string, baseUnit: number, posTokens: PosToken[]): { text: string; removed: PosToken[] } {
  const localTokens = tokensInRange(posTokens, baseUnit, baseUnit + text.length);
  const removed = localTokens.filter(removableByPos);
  if (!removed.length) return { text, removed: [] };
  let output = "";
  let cursor = 0;
  for (const token of removed) {
    const start = token.start - baseUnit;
    const end = token.end - baseUnit;
    output += text.slice(cursor, start);
    cursor = end;
  }
  output += text.slice(cursor);
  if (!output.trim()) return { text, removed: [] };
  return { text: output.replace(/[ \t]{2,}/g, " "), removed };
}

function contentParts(text: string, posTokens: PosToken[], baseUnit: number): ReturnType<typeof sentenceParts> {
  return mergeEnumerationParts(text, clauseParts(text), posTokens, baseUnit);
}

function isCompressible(text:string, parts:ReturnType<typeof sentenceParts>):boolean {
  return parts.length >= 3 && ([...text].length > 80 || (text.match(/[A-Za-z][A-Za-z'-]*/g)?.length ?? 0) > 60);
}

export function summarize(text: string, maxBlocks: number, posTokens: PosToken[] = []): SummaryDocument {
  const input=text; const parsedBlocks=parseBlocks(input);
  const blocks:SummaryBlock[]=parsedBlocks.map((p,index)=>{ const sourceText=input.slice(p.start,p.end); const listItem=p.kind.endsWith("list-item"); return { index,kind:p.kind,byteStart:p.byteStart,byteEnd:p.byteEnd,depth:p.depth,decision:"omit",sourceText,outputText:"",selectedSpans:[],removedTokens:[],signals:signals(sourceText,p.byteStart,listItem,posTokens,p.start),score:null,children:[] }; });
  let candidates:Candidate[]=blocks.filter((b)=>b.kind==="paragraph"||b.kind==="blockquote").map((b)=>{const terms=termMap(b.sourceText); return {blockIndex:b.index,terms,length:[...terms.values()].reduce((a,b)=>a+b,0)};}).filter((c)=>c.length>0);const totalCandidates=candidates.length;
  const candidateCap=Math.max(256,Math.min(1024,Math.max(0,Math.trunc(maxBlocks))*2));if(candidates.length>candidateCap){const priorityCount=Math.floor(candidateCap*3/4),sampleCount=candidateCap-priorityCount;const ranked=candidates.map((_,i)=>i).sort((a,b)=>{const proxy=(i:number)=>.6*discourse(blocks[candidates[i].blockIndex].sourceText)+.3*signalScore(blocks[candidates[i].blockIndex].signals)+.1*Math.min(100,candidates[i].length)/100;return proxy(b)-proxy(a)||candidates[a].blockIndex-candidates[b].blockIndex;});const keep=new Set(ranked.slice(0,priorityCount));for(let i=0;i<sampleCount;i+=1)keep.add(sampleCount===1?Math.floor(candidates.length/2):Math.floor(i*(candidates.length-1)/(sampleCount-1)));candidates=candidates.filter((_,i)=>keep.has(i));}
  const documentFrequency=new Map<string,number>();for(const candidate of candidates)for(const term of candidate.terms.keys())documentFrequency.set(term,(documentFrequency.get(term)??0)+1);const averageLength=candidates.reduce((sum,c)=>sum+c.length,0)/Math.max(1,candidates.length);const matrix=candidates.map((a,i)=>candidates.map((b,j)=>i===j?0:bm25(a,b,documentFrequency,candidates.length,averageLength))); let ranks=candidates.map(()=>1/Math.max(1,candidates.length));
  for(let k=0;k<50;k+=1) ranks=candidates.map((_,target)=>.15/Math.max(1,candidates.length)+.85*candidates.reduce((sum,__,source)=>{const total=matrix[source].reduce((a,b)=>a+b,0); return sum+(total?matrix[source][target]/total*ranks[source]:ranks[source]/Math.max(1,candidates.length));},0));
  const maxRank=Math.max(...ranks,1e-6); const chosen:number[]=[]; const coverage=candidates.map(()=>0);
  while(chosen.length<Math.min(Math.max(0,Math.trunc(maxBlocks)||0),candidates.length)) { let best=-1,bestScore=-1,bestParts:SummaryScore|null=null;
    for(let i=0;i<candidates.length;i+=1){if(chosen.includes(i))continue; const overlap=chosen.length?Math.max(...chosen.map((c)=>matrix[i][c])):0;if(overlap>=.8)continue;const novelty=1-overlap;const gain=matrix[i].reduce((sum,v,j)=>sum+Math.max(0,(i===j?1:v)-coverage[j]),0)/Math.max(1,candidates.length);const disc=discourse(blocks[candidates[i].blockIndex].sourceText);const relevance=.7*ranks[i]/maxRank+.3*disc;const sig=signalScore(blocks[candidates[i].blockIndex].signals);const finalScore=.35*relevance+.3*disc+.1*gain+.05*novelty+.2*sig;if(finalScore>bestScore){best=i;bestScore=finalScore;bestParts={relevance,coverageGain:gain,novelty,signal:sig,finalScore};}}
    if(best<0)break;chosen.push(best);for(let j=0;j<coverage.length;j+=1)coverage[j]=Math.max(coverage[j],best===j?1:matrix[best][j]);blocks[candidates[best].blockIndex].score=bestParts;
  }
  let preserved=0,forced=0;
  const exact=(b:SummaryBlock,decision:Decision)=>{b.decision=decision;b.outputText=b.sourceText;b.selectedSpans=[{text:b.sourceText,byteStart:b.byteStart,byteEnd:b.byteEnd,forcedByNegation:false}];};
  const compactExactProse=(b:SummaryBlock)=>{const compacted=compactByPos(b.outputText,parsedBlocks[b.index].start,posTokens);if(compacted.removed.length){b.outputText=compacted.text;b.removedTokens=compacted.removed;b.decision="compact_pos";}};
  const within=(b:SummaryBlock,limit:number,list:boolean)=>{
    const childLines=b.sourceText.split(/\r?\n/);
    if(list&&(childLines.some((line)=>Boolean(fence(line)))||childLines.slice(1).some((line)=>/^(?: {4}|\t)/.test(line)||htmlStart(line)))){exact(b,"preserve_exact");return;}
    const blockUnitStart=parsedBlocks[b.index].start;
    const parts=contentParts(b.sourceText,posTokens,blockUnitStart);
    if(!isCompressible(b.sourceText,parts)){exact(b,list?"preserve_exact":"select_exact");if(!list)compactExactProse(b);return;}
    const localLimit=Math.max(1,Math.min(limit,Math.ceil(parts.length/2)));
    const ranked=parts.map((p,i)=>{const partSignals=signals(p.text,b.byteStart+byteAt(b.sourceText,p.start),list,posTokens,blockUnitStart+p.start);return {i,p,signals:partSignals,s:.35*discourse(p.text)+.5*signalScore(partSignals)+.15*(1-i/parts.length*.25)}}).sort((a,b)=>b.s-a.s||a.i-b.i);
    const baseline=new Set(ranked.slice(0,localLimit).map((r)=>r.i));
    const selected=new Set(baseline);
    let forcedWithin=0;
    for(const r of ranked)if(r.signals.negationCount&&!selected.has(r.i)){selected.add(r.i);forcedWithin+=1;}
    const ordered=[...selected].sort((a,b)=>a-b);
    if(ordered.length>=parts.length){exact(b,list?"preserve_exact":"select_exact");if(!list)compactExactProse(b);return;}
    b.decision="summarize_within";
    forced+=forcedWithin;
    b.selectedSpans=ordered.map((i)=>({text:parts[i].text,byteStart:b.byteStart+byteAt(b.sourceText,parts[i].start),byteEnd:b.byteStart+byteAt(b.sourceText,parts[i].end),forcedByNegation:!baseline.has(i)}));
    const compactedParts=ordered.map((i)=>compactByPos(parts[i].text,blockUnitStart+parts[i].start,posTokens));
    b.removedTokens=compactedParts.flatMap((part)=>part.removed);
    b.outputText=compactedParts.map((part)=>part.text).join(" ");
    if(list){const marker=listInfo(b.sourceText);if(marker&&!listInfo(b.outputText))b.outputText=b.sourceText.slice(0,marker.markerEnd)+b.outputText.trimStart();}
  };
  for(const b of blocks){if(["fenced-code","indented-code","table","html"].includes(b.kind)){exact(b,"preserve_exact");preserved+=1;}else if(b.kind.endsWith("list-item")){within(b,2,true);preserved+=1;}}
  for(const i of chosen)within(blocks[candidates[i].blockIndex],2,false);
  for(let i=0;i<blocks.length;i+=1)if(blocks[i].kind==="heading"){const end=blocks.slice(i+1).findIndex((b)=>b.kind==="heading"&&b.depth<=blocks[i].depth);const section=blocks.slice(i+1,end<0?blocks.length:i+1+end);if(section.some((b)=>b.decision!=="omit"))exact(blocks[i],"context_only");}
  for(let i=0;i<blocks.length;i+=1)if(blocks[i].kind==="thematic-break"&&blocks.slice(0,i).some((b)=>b.decision!=="omit")&&blocks.slice(i+1).some((b)=>b.decision!=="omit"))exact(blocks[i],"context_only");
  for(let i=0;i<blocks.length;i+=1)if(blocks[i].kind.endsWith("list-item")){let end=i+1;while(end<blocks.length&&(!blocks[end].kind.endsWith("list-item")||blocks[end].depth>blocks[i].depth))end+=1;const deeper=blocks.slice(i+1,end).filter((b)=>b.kind.endsWith("list-item")&&b.depth>blocks[i].depth);const childDepth=deeper.length?Math.min(...deeper.map((b)=>b.depth)):null;blocks[i].children=childDepth===null?[]:deeper.filter((b)=>b.depth===childDepth).map((b)=>({...b,children:[]}));}
  let output="",previous:SummaryBlock|null=null;for(const b of blocks.filter((b)=>b.decision!=="omit"&&b.outputText)){if(previous){const gap=input.slice(parsedBlocks[previous.index].end,parsedBlocks[b.index].start);output+=gap&&/^\s*$/.test(gap)?gap:output.endsWith("\n")?"":"\n\n";}output+=b.outputText;previous=b;}
  const overflowReasons:string[]=[];if(candidates.length<totalCandidates)overflowReasons.push(`${totalCandidates} 個可排名區塊先以 portable proxy 收斂為 ${candidates.length} 個，再建立 TextRank 圖`);if(forced)overflowReasons.push(`${forced} 個有效否定子句超過局部 clause 上限並依語意閉包保留`);if(preserved)overflowReasons.push(`${preserved} 個 code/list/table/HTML block 不計入 maxBlocks`);
  const inputChars=[...input].length,outputChars=[...output].length;
  return {schemaVersion:2,mode:"hierarchical-extractive",text:output,blocks,budget:{requestedMaxBlocks:maxBlocks,selectedRankedBlocks:chosen.length,preservedBlocks:preserved,forcedNegationClauses:forced,actualOutputBlocks:blocks.filter((b)=>b.decision!=="omit").length,overflowReasons},inputChars,outputChars,reductionPercent:inputChars?Math.max(0,(1-outputChars/inputChars)*100):0};
}
