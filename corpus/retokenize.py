"""把 CKIP WS/POS/NER 標註重建成一致的 canonical token 序列。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


NAMED_ENTITY_LABELS = frozenset(
    {
        "PERSON",
        "ORG",
        "GPE",
        "LOC",
        "FAC",
        "NORP",
        "PRODUCT",
        "EVENT",
        "WORK_OF_ART",
        "LAW",
        "LANGUAGE",
    }
)
QUANTITY_ENTITY_LABELS = frozenset(
    {"CARDINAL", "ORDINAL", "QUANTITY", "MONEY", "PERCENT", "DATE", "TIME"}
)
QUANTITY_POS = {
    "CARDINAL": "Neu",
    "ORDINAL": "Neu",
    "QUANTITY": "Neu",
    "MONEY": "Neu",
    "PERCENT": "Neu",
    "DATE": "Nd",
    "TIME": "Nd",
}

# 只列出明確具有後綴生產力、且可由語料支持度進一步約束的名詞語素。
NOUN_SUFFIX_MORPHEMES = frozenset({"師", "員", "家", "者", "手"})

_ASCII_YEAR_RE = re.compile(r"(?<![0-9])[12][0-9]{3}(?=年)")
_HAN_YEAR_RE = re.compile(r"[〇零一二兩三四五六七八九壹貳參肆伍陸柒捌玖]{4}(?=年)")
_ROC_ASCII_YEAR_RE = re.compile(r"民國[0-9]{1,3}(?=年)")
_ROC_HAN_YEAR_RE = re.compile(
    r"民國[〇零一二兩三四五六七八九十百千壹貳參肆伍陸柒捌玖拾佰仟]+(?=年)"
)
_NUMERIC_EXPRESSION_RE = re.compile(
    r"[0-9〇零一二兩三四五六七八九十百千萬億兆壹貳參肆伍陸柒捌玖拾佰仟多餘余約.,/]+"
)


def is_han(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FA1F
    )


def is_han_word(word: str) -> bool:
    return bool(word) and all(is_han(char) for char in word)


def is_dictionary_surface(word: str) -> bool:
    """允許純漢字，以及不含空白／標點的漢字英數混合 canonical 詞。"""

    return bool(word) and any(is_han(char) for char in word) and all(
        is_han(char) or char.isalnum() or char in {".", "%", "％", "-", "_"}
        for char in word
    )


def is_quantity_surface(text: str) -> bool:
    """數量 span 可含格式符號，但拒絕括號與一般標點。"""

    allowed = {".", ",", ":", "/", "%", "％", "-", "+", "$", "＄", "¥", "￥"}
    return bool(text) and not any(char.isspace() for char in text) and all(
        is_han(char) or char.isalnum() or char in allowed for char in text
    )


@dataclass(frozen=True)
class AlignedToken:
    text: str
    pos: str | None
    start: int
    end: int


@dataclass(frozen=True)
class EntitySpan:
    text: str
    label: str
    start: int
    end: int
    token_start: int
    token_end: int


@dataclass(frozen=True)
class CanonicalToken:
    text: str
    pos: str | None
    start: int
    end: int
    reason: str = "original"
    dictionary_eligible: bool = True
    canonical: str | None = None
    label: str | None = None


@dataclass
class RetokenizationEvidence:
    entity_counts: Counter[tuple[str, str]]
    suffix_pair_counts: Counter[str]
    whole_word_counts: Counter[str]
    invalid_entity_boundaries: Counter[str]
    source: str = "annotations"

    @classmethod
    def empty(cls, source: str = "annotations") -> "RetokenizationEvidence":
        return cls(Counter(), Counter(), Counter(), Counter(), source)


@dataclass(frozen=True)
class RetokenizationPolicy:
    approved_named_entities: Mapping[str, str]
    approved_suffix_words: frozenset[str]
    min_entity_support: int
    min_entity_confidence: float
    min_morpheme_support: int

    def report(self, evidence: RetokenizationEvidence) -> dict[str, Any]:
        return {
            "version": "canonical-v2",
            "evidence_source": evidence.source,
            "approved_named_entities": len(self.approved_named_entities),
            "approved_suffix_words": len(self.approved_suffix_words),
            "valid_entity_surfaces": len({word for word, _ in evidence.entity_counts}),
            "invalid_entity_boundaries": dict(sorted(evidence.invalid_entity_boundaries.items())),
            "min_entity_support": self.min_entity_support,
            "min_entity_confidence": self.min_entity_confidence,
            "min_morpheme_support": self.min_morpheme_support,
            "quantity_representative": "數值以一／1聚合；公元年份以2000聚合",
            "noun_suffix_morphemes": sorted(NOUN_SUFFIX_MORPHEMES),
        }


def align_tokens(record: Mapping[str, Any]) -> list[AlignedToken]:
    original = str(record.get("text", ""))
    aligned: list[AlignedToken] = []
    cursor = 0
    for raw in record.get("tokens", ()):
        word = str(raw.get("text", ""))
        if not word:
            continue
        start = original.find(word, cursor)
        if start < 0:
            # 保留原 CKIP token 供模型使用，但不讓無法證明的 NER span 自動合併。
            start = cursor
        end = start + len(word)
        pos = raw.get("ckip_pos")
        aligned.append(AlignedToken(word, str(pos) if pos else None, start, end))
        cursor = end
    return aligned


def valid_entity_spans(
    record: Mapping[str, Any], tokens: Sequence[AlignedToken]
) -> tuple[list[EntitySpan], Counter[str]]:
    original = str(record.get("text", ""))
    start_indices = {token.start: index for index, token in enumerate(tokens)}
    end_indices = {token.end: index + 1 for index, token in enumerate(tokens)}
    valid: list[EntitySpan] = []
    invalid: Counter[str] = Counter()
    for raw in record.get("entities", ()):
        label = str(raw.get("label", ""))
        text = str(raw.get("text", ""))
        try:
            start, end = int(raw.get("start")), int(raw.get("end"))
        except (TypeError, ValueError):
            invalid["invalid-offset"] += 1
            continue
        if not (0 <= start < end <= len(original)) or original[start:end] != text:
            invalid["surface-mismatch"] += 1
            continue
        token_start = start_indices.get(start)
        token_end = end_indices.get(end)
        if token_start is None or token_end is None or token_start >= token_end:
            invalid["partial-token-boundary"] += 1
            continue
        covered = tokens[token_start:token_end]
        if any(left.end != right.start for left, right in zip(covered, covered[1:])):
            invalid["non-contiguous-token-span"] += 1
            continue
        if "".join(token.text for token in covered) != text:
            invalid["token-surface-mismatch"] += 1
            continue
        valid.append(EntitySpan(text, label, start, end, token_start, token_end))
    return valid, invalid


def add_record_evidence(record: Mapping[str, Any], evidence: RetokenizationEvidence) -> None:
    tokens = align_tokens(record)
    spans, invalid = valid_entity_spans(record, tokens)
    evidence.invalid_entity_boundaries.update(invalid)
    for span in spans:
        if span.label in NAMED_ENTITY_LABELS:
            evidence.entity_counts[(span.text, span.label)] += 1
    for token in tokens:
        if is_han_word(token.text):
            evidence.whole_word_counts[token.text] += 1
    for previous, suffix in zip(tokens, tokens[1:]):
        if (
            previous.end == suffix.start
            and suffix.text in NOUN_SUFFIX_MORPHEMES
            and suffix.pos is not None
            and suffix.pos.startswith("N")
            and is_han_word(previous.text)
        ):
            evidence.suffix_pair_counts[previous.text + suffix.text] += 1


def build_policy(
    evidence: RetokenizationEvidence,
    *,
    min_entity_support: int,
    min_entity_confidence: float,
    min_morpheme_support: int,
) -> RetokenizationPolicy:
    by_surface: dict[str, Counter[str]] = defaultdict(Counter)
    for (surface, label), count in evidence.entity_counts.items():
        by_surface[surface][label] += count
    approved_entities: dict[str, str] = {}
    for surface, labels in by_surface.items():
        ranked = labels.most_common()
        label, support = ranked[0]
        total = sum(labels.values())
        if support >= min_entity_support and support / total >= min_entity_confidence:
            approved_entities[surface] = label

    suffix_candidates = set(evidence.suffix_pair_counts) | {
        surface
        for surface in evidence.whole_word_counts
        if surface and surface[-1] in NOUN_SUFFIX_MORPHEMES
    }
    approved_suffixes = {
        surface
        for surface in suffix_candidates
        if evidence.suffix_pair_counts[surface] >= min_morpheme_support
        or evidence.whole_word_counts[surface] >= min_morpheme_support
    }
    return RetokenizationPolicy(
        approved_entities,
        frozenset(approved_suffixes),
        min_entity_support,
        min_entity_confidence,
        min_morpheme_support,
    )


def canonical_quantity(text: str, label: str) -> str:
    """將生成性數值映射到少數代表型，但不改動訓練用 surface。"""

    ascii_year = "\ufff0ASCII_YEAR\ufff1"
    han_year = "\ufff0HAN_YEAR\ufff1"
    roc_ascii_year = "\ufff0ROC_ASCII_YEAR\ufff1"
    roc_han_year = "\ufff0ROC_HAN_YEAR\ufff1"
    percent_of = "\ufff0PERCENT_OF\ufff1"
    value = _ROC_ASCII_YEAR_RE.sub(roc_ascii_year, text)
    value = _ROC_HAN_YEAR_RE.sub(roc_han_year, value)
    if label == "DATE":
        value = _ASCII_YEAR_RE.sub(ascii_year, value)
        value = _HAN_YEAR_RE.sub(han_year, value)
    value = value.replace("百分之", percent_of)

    def replace_number(match: re.Match[str]) -> str:
        return "1" if any(char.isascii() and char.isdigit() for char in match.group()) else "一"

    value = _NUMERIC_EXPRESSION_RE.sub(replace_number, value)
    return (
        value.replace(percent_of, "百分之")
        .replace(ascii_year, "2000")
        .replace(han_year, "二〇〇〇")
        .replace(roc_ascii_year, "民國100")
        .replace(roc_han_year, "民國一百")
    )


def _entity_pos(span: EntitySpan, tokens: Sequence[AlignedToken]) -> str:
    if span.label == "PERSON":
        return "Nb"
    covered = tokens[span.token_start : span.token_end]
    for token in reversed(covered):
        if token.pos:
            return token.pos
    return "Nb"


def _select_spans(
    spans: Iterable[EntitySpan], policy: RetokenizationPolicy
) -> list[tuple[EntitySpan, str]]:
    candidates: list[tuple[int, int, EntitySpan, str]] = []
    for span in spans:
        if (
            span.label in NAMED_ENTITY_LABELS
            and policy.approved_named_entities.get(span.text) == span.label
            and span.token_end - span.token_start >= 2
        ):
            candidates.append((300, span.end - span.start, span, "named-entity"))
        elif span.label in QUANTITY_ENTITY_LABELS and is_quantity_surface(span.text):
            candidates.append((200, span.end - span.start, span, "quantity"))
    candidates.sort(key=lambda row: (-row[0], -row[1], row[2].start, row[2].end))
    selected: list[tuple[EntitySpan, str]] = []
    occupied: list[tuple[int, int]] = []
    for _, _, span, reason in candidates:
        if any(span.start < end and start < span.end for start, end in occupied):
            continue
        occupied.append((span.start, span.end))
        selected.append((span, reason))
    return sorted(selected, key=lambda row: row[0].start)


def canonicalize_record(
    record: Mapping[str, Any], policy: RetokenizationPolicy
) -> tuple[list[CanonicalToken], list[EntitySpan], Counter[str]]:
    tokens = align_tokens(record)
    spans, invalid = valid_entity_spans(record, tokens)
    selected = {
        span.token_start: (span, reason) for span, reason in _select_spans(spans, policy)
    }
    canonical: list[CanonicalToken] = []
    index = 0
    while index < len(tokens):
        choice = selected.get(index)
        if choice is None:
            token = tokens[index]
            canonical.append(CanonicalToken(token.text, token.pos, token.start, token.end))
            index += 1
            continue
        span, reason = choice
        pos = QUANTITY_POS[span.label] if reason == "quantity" else _entity_pos(span, tokens)
        canonical.append(
            CanonicalToken(
                span.text,
                pos,
                span.start,
                span.end,
                reason=reason,
                dictionary_eligible=reason != "quantity",
                canonical=canonical_quantity(span.text, span.label)
                if reason == "quantity"
                else None,
                label=span.label,
            )
        )
        index = span.token_end

    repaired: list[CanonicalToken] = []
    for token in canonical:
        if (
            repaired
            and token.reason == "original"
            and repaired[-1].reason == "original"
            and token.text in NOUN_SUFFIX_MORPHEMES
            and token.pos is not None
            and token.pos.startswith("N")
            and repaired[-1].end == token.start
            and repaired[-1].text + token.text in policy.approved_suffix_words
        ):
            previous = repaired.pop()
            repaired.append(
                CanonicalToken(
                    previous.text + token.text,
                    token.pos,
                    previous.start,
                    token.end,
                    reason="noun-suffix",
                )
            )
        else:
            repaired.append(token)
    return repaired, spans, invalid
