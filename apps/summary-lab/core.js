export const SIGNALS = [
  { key: "properNounCount", label: "專有名詞", className: "signal--proper" },
  { key: "negationCount", label: "否定", className: "signal--negation" },
  { key: "emphasisCount", label: "強調", className: "signal--emphasis" },
  { key: "listItem", label: "條列", className: "signal--list" },
  { key: "objectNameCount", label: "物件名", className: "signal--object" },
  { key: "dateCount", label: "日期", className: "signal--date" },
  { key: "numberCount", label: "數字", className: "signal--number" },
  { key: "quantityCount", label: "數值＋單位", className: "signal--quantity" },
  { key: "moneyCount", label: "金額", className: "signal--quantity" },
  { key: "acronymCount", label: "縮略語", className: "signal--acronym" }
];

export function activeSignals(signals = {}) {
  return SIGNALS.filter(({ key }) => key === "listItem" ? Boolean(signals[key]) : Number(signals[key]) > 0);
}

export function buildCumulativeCurve(blocks = []) {
  const ranked = blocks
    .filter((block) => Number.isFinite(block.score?.finalScore))
    .slice()
    .sort((a, b) => b.score.finalScore - a.score.finalScore || a.index - b.index);
  const total = ranked.reduce((sum, block) => sum + Math.max(0, block.score.finalScore), 0);
  let cumulative = 0;
  return ranked.map((block, index) => {
    cumulative += Math.max(0, block.score.finalScore);
    return {
      rank: index + 1,
      blockIndex: block.index,
      selected: block.decision !== "omit",
      cumulativeShare: total > 0 ? cumulative / total : 0
    };
  });
}

export function curvePath(points, width = 720, height = 220, inset = 28) {
  if (!points.length) return "";
  const usableWidth = width - inset * 2;
  const usableHeight = height - inset * 2;
  return points.map((point, index) => {
    const x = points.length === 1 ? inset + usableWidth / 2 : inset + index / (points.length - 1) * usableWidth;
    const y = height - inset - point.cumulativeShare * usableHeight;
    return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

export function formatDuration(value) {
  if (!Number.isFinite(value)) return "—";
  if (value < 1) return `${value.toFixed(2)} ms`;
  if (value < 100) return `${value.toFixed(1)} ms`;
  return `${Math.round(value)} ms`;
}

export function formatScore(value) {
  return Number.isFinite(value) ? value.toFixed(3) : "—";
}

export function reductionLabel(inputTokens, outputTokens) {
  if (!inputTokens) return "0.0%";
  return `${((1 - outputTokens / inputTokens) * 100).toFixed(1)}%`;
}
