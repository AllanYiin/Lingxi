from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .pipeline import SCHEMA_VERSION, is_han, read_jsonl, write_jsonl

BMES = ("B", "M", "E", "S")
MIN_LOG = -3.14e100
LEGAL_NEXT = {
    "B": ("M", "E"),
    "M": ("M", "E"),
    "E": ("B", "S"),
    "S": ("B", "S"),
}


def validate_records(records: Iterable[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for row_number, record in enumerate(records, 1):
        label = str(record.get("id", f"row-{row_number}"))
        if record.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{label}: unsupported schema_version")
        if label in seen_ids:
            errors.append(f"{label}: duplicate id")
        seen_ids.add(label)
        if record.get("split") not in {"train", "dev", "test"}:
            errors.append(f"{label}: invalid split")
        text = record.get("text")
        if not isinstance(text, str) or not text:
            errors.append(f"{label}: text must be a non-empty string")
            continue
        review = record.get("review") or {}
        if review.get("status") not in {"pending", "accepted", "rejected"}:
            errors.append(f"{label}: invalid review.status")
        if review.get("status") != "accepted":
            continue
        tokens = review.get("tokens")
        if not isinstance(tokens, list) or not tokens:
            errors.append(f"{label}: accepted record requires review.tokens")
            continue
        rebuilt = ""
        for token_index, token in enumerate(tokens):
            token_text = token.get("text") if isinstance(token, Mapping) else None
            if not isinstance(token_text, str) or not token_text:
                errors.append(f"{label}: token {token_index} has invalid text")
                continue
            rebuilt += token_text
            if all(is_han(char) for char in token_text):
                pos = token.get("pos")
                if not isinstance(pos, str) or not pos.strip():
                    errors.append(f"{label}: Han token {token_index} requires pos")
        if rebuilt != text:
            errors.append(f"{label}: reviewed tokens do not reconstruct text exactly")
    return errors


def bmes_for_word(word: str) -> list[str]:
    if len(word) == 1:
        return ["S"]
    if len(word) == 2:
        return ["B", "E"]
    return ["B", *("M" for _ in range(len(word) - 2)), "E"]


def accepted_sequences(
    records: Iterable[Mapping[str, Any]], include_splits: set[str]
) -> Iterator[list[tuple[str, str]]]:
    for record in records:
        if record.get("split") not in include_splits:
            continue
        review = record.get("review") or {}
        if review.get("status") != "accepted":
            continue
        sequence: list[tuple[str, str]] = []
        for token in review["tokens"]:
            word, pos = token["text"], token.get("pos")
            if pos and all(is_han(char) for char in word):
                sequence.append((word, pos))
            elif sequence:
                yield sequence
                sequence = []
        if sequence:
            yield sequence


def log_row(
    counts: Mapping[str, int], allowed: Sequence[str], alpha: float
) -> dict[str, float]:
    total = sum(counts.get(key, 0) for key in allowed) + alpha * len(allowed)
    if total <= 0:
        return {key: MIN_LOG for key in allowed}
    return {key: math.log((counts.get(key, 0) + alpha) / total) for key in allowed}


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_dictionary(
    sequences: Sequence[Sequence[tuple[str, str]]],
    output_dir: Path,
    min_support: int,
    min_confidence: float,
    min_margin: float,
) -> dict[str, int]:
    evidence: dict[str, Counter[str]] = defaultdict(Counter)
    for sequence in sequences:
        for word, pos in sequence:
            evidence[word][pos] += 1
    dictionary: dict[str, list[Any]] = {}
    rows = []
    review_rows = []
    for word in sorted(evidence):
        counts = evidence[word]
        ranked = counts.most_common()
        top_tag, top_count = ranked[0]
        total = sum(counts.values())
        second_count = ranked[1][1] if len(ranked) > 1 else 0
        confidence = top_count / total
        margin = (top_count - second_count) / total
        accepted = (
            total >= min_support
            and confidence >= min_confidence
            and margin >= min_margin
        )
        if total < min_support:
            reason = "insufficient_support"
        elif confidence < min_confidence:
            reason = "low_confidence"
        elif margin < min_margin:
            reason = "small_margin"
        else:
            reason = "accepted"
        row = {
            "word": word,
            "tag_counts": dict(sorted(counts.items())),
            "support": total,
            "primary_tag": top_tag,
            "confidence": confidence,
            "margin": margin,
            "accepted": accepted,
            "reason": reason,
        }
        rows.append(row)
        if accepted:
            dictionary[word] = [top_tag, total]
        else:
            review_rows.append(row)
    dump_json(output_dir / "Dict.json", dictionary)
    write_jsonl(output_dir / "Dict.evidence.jsonl", rows)
    write_jsonl(output_dir / "Dict.review.jsonl", review_rows)
    return {
        "dictionary_entries": len(dictionary),
        "dictionary_review_queue": len(review_rows),
    }


def build_bmes(
    sequences: Sequence[Sequence[tuple[str, str]]], output_dir: Path, alpha: float
) -> None:
    start: Counter[str] = Counter()
    trans1: dict[str, Counter[str]] = defaultdict(Counter)
    trans2: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    emit1: dict[str, Counter[str]] = defaultdict(Counter)
    emit2: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    reverse_emit: dict[str, Counter[str]] = defaultdict(Counter)
    for sequence in sequences:
        states: list[str] = []
        chars: list[str] = []
        for word, _ in sequence:
            word_states = bmes_for_word(word)
            states.extend(word_states)
            chars.extend(word)
        if not states:
            continue
        start[states[0]] += 1
        for index, (char, state) in enumerate(zip(chars, states)):
            emit1[state][char] += 1
            reverse_emit[char][state] += 1
            if index >= 1:
                trans1[states[index - 1]][state] += 1
                emit2[(states[index - 1], state)][char] += 1
            if index >= 2:
                trans2[(states[index - 2], states[index - 1])][state] += 1
    start_probs = {state: MIN_LOG for state in BMES}
    start_probs.update(log_row(start, ("B", "S"), alpha))
    trans_probs = {
        previous: {
            state: log_row(trans1[previous], LEGAL_NEXT[previous], alpha).get(
                state, MIN_LOG
            )
            for state in BMES
        }
        for previous in BMES
    }
    trans2_probs = {
        prev2: {
            prev1: {
                state: log_row(trans2[(prev2, prev1)], LEGAL_NEXT[prev1], alpha).get(
                    state, MIN_LOG
                )
                for state in BMES
            }
            for prev1 in BMES
        }
        for prev2 in BMES
    }
    emit1_probs = {
        state: log_row(counts, tuple(sorted(counts)), alpha)
        for state, counts in sorted(emit1.items())
    }
    emit2_probs = {
        previous: {
            current: log_row(
                emit2[(previous, current)],
                tuple(sorted(emit2[(previous, current)])),
                alpha,
            )
            for current in BMES
            if emit2[(previous, current)]
        }
        for previous in BMES
    }
    reverse_probs = {
        char: {
            state: (counts.get(state, 0) + alpha)
            / (sum(counts.values()) + alpha * len(BMES))
            for state in BMES
        }
        for char, counts in sorted(reverse_emit.items())
    }
    dump_json(output_dir / "startProbs.json", start_probs)
    dump_json(output_dir / "transProbs.json", trans_probs)
    dump_json(output_dir / "transProbs2.json", trans2_probs)
    dump_json(output_dir / "emmitProbs.json", emit1_probs)
    dump_json(output_dir / "emmitProbs2.json", emit2_probs)
    dump_json(output_dir / "r_emmitProbs.json", reverse_probs)


def joint_state_candidates(tags: Sequence[str]) -> list[str]:
    return [f"{state}-{tag}" for tag in tags for state in BMES]


def legal_joint_next(previous: str, candidate: str) -> bool:
    previous_state, previous_tag = previous.split("-", 1)
    state, tag = candidate.split("-", 1)
    if previous_state in {"B", "M"}:
        return tag == previous_tag and state in {"M", "E"}
    return state in {"B", "S"}


def build_pos(
    sequences: Sequence[Sequence[tuple[str, str]]], output_dir: Path, alpha: float
) -> None:
    start: Counter[str] = Counter()
    transitions: dict[str, Counter[str]] = defaultdict(Counter)
    emissions: dict[str, Counter[str]] = defaultdict(Counter)
    char_states: dict[str, set[str]] = defaultdict(set)
    tags = sorted({pos for sequence in sequences for _, pos in sequence})
    states = joint_state_candidates(tags)
    for sequence in sequences:
        joint: list[str] = []
        chars: list[str] = []
        for word, pos in sequence:
            word_states = [f"{state}-{pos}" for state in bmes_for_word(word)]
            joint.extend(word_states)
            chars.extend(word)
        if not joint:
            continue
        start[joint[0]] += 1
        for index, (char, state) in enumerate(zip(chars, joint)):
            emissions[state][char] += 1
            char_states[char].add(state)
            if index:
                transitions[joint[index - 1]][state] += 1
    allowed_start = [state for state in states if state.startswith(("B-", "S-"))]
    start_probs = {state: MIN_LOG for state in states}
    start_probs.update(log_row(start, allowed_start, alpha))
    trans_probs: dict[str, dict[str, float]] = {}
    for previous in states:
        allowed = [
            candidate for candidate in states if legal_joint_next(previous, candidate)
        ]
        row = log_row(transitions[previous], allowed, alpha)
        trans_probs[previous] = {
            candidate: row.get(candidate, MIN_LOG) for candidate in states
        }
    emit_probs = {
        state: log_row(emissions[state], tuple(sorted(emissions[state])), alpha)
        for state in states
        if emissions[state]
    }
    char_state_table = {
        char: sorted(observed_states)
        for char, observed_states in sorted(char_states.items())
    }
    dump_json(output_dir / "tagStartProbs.json", start_probs)
    dump_json(output_dir / "tagTransProbs.json", trans_probs)
    dump_json(output_dir / "tagEmitProbs.json", emit_probs)
    dump_json(output_dir / "char_state_tab.json", char_state_table)


def build_models(
    input_path: Path,
    output_dir: Path,
    include_splits: set[str] | None = None,
    alpha: float = 0.1,
    min_support: int = 10,
    min_confidence: float = 0.8,
    min_margin: float = 0.2,
) -> dict[str, Any]:
    records = list(read_jsonl(input_path))
    errors = validate_records(records)
    if errors:
        preview = "\n".join(errors[:20])
        raise ValueError(
            f"training data validation failed ({len(errors)} errors):\n{preview}"
        )
    include_splits = include_splits or {"train"}
    sequences = list(accepted_sequences(records, include_splits))
    if not sequences:
        raise ValueError("no accepted Han-token sequences in selected splits")
    output_dir.mkdir(parents=True, exist_ok=True)
    dump_json(output_dir / "VariantWords.json", {})
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "included_splits": sorted(include_splits),
        "accepted_sequences": len(sequences),
        "accepted_tokens": sum(len(sequence) for sequence in sequences),
        "smoothing_alpha": alpha,
    }
    report.update(
        build_dictionary(
            sequences,
            output_dir,
            min_support=min_support,
            min_confidence=min_confidence,
            min_margin=min_margin,
        )
    )
    build_bmes(sequences, output_dir, alpha)
    build_pos(sequences, output_dir, alpha)
    dump_json(output_dir / "training-report.json", report)
    return report
