"""將 CKIP 標註 shard 串流彙總成 LingXi 詞典、二階 BMES 與 POS HMM。"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from corpus.process_corpus import SCHEMA_VERSION
from corpus.retokenize import (
    NAMED_ENTITY_LABELS,
    NOUN_SUFFIX_MORPHEMES,
    QUANTITY_ENTITY_LABELS,
    RetokenizationEvidence,
    RetokenizationPolicy,
    add_record_evidence,
    build_policy,
    canonicalize_record,
    is_dictionary_surface,
)


BMES = ("B", "M", "E", "S")
MIN_LOG = -3.14e100
CKIP_ENTITY_LABELS = NAMED_ENTITY_LABELS
CKIP_ENTITY_DEFAULT_POS = {
    "PERSON": "Nb",
    "ORG": "Nc",
    "GPE": "Nc",
    "LOC": "Nc",
    "FAC": "Nc",
    "NORP": "Na",
    "PRODUCT": "Na",
    "EVENT": "Na",
    "WORK_OF_ART": "Na",
    "LAW": "Na",
    "LANGUAGE": "Na",
}
LEGAL_NEXT = {
    "B": ("M", "E"),
    "M": ("M", "E"),
    "E": ("B", "S"),
    "S": ("B", "S"),
}


@dataclass
class ModelCounts:
    bmes_start: Counter[str]
    bmes_trans1: dict[str, Counter[str]]
    bmes_trans2: dict[tuple[str, str], Counter[str]]
    bmes_emit1: dict[str, Counter[str]]
    bmes_emit2: dict[tuple[str, str], Counter[str]]
    bmes_reverse: dict[str, Counter[str]]
    pos_start: Counter[str]
    pos_trans1: dict[str, Counter[str]]
    pos_trans2: dict[tuple[str, str], Counter[str]]
    pos_emit: dict[str, Counter[str]]
    char_states: dict[str, set[str]]
    source_records: Counter[str]
    source_tokens: Counter[str]
    source_original_tokens: Counter[str]
    entity_labels: Counter[str]
    retokenization: Counter[str]
    quantity_patterns: Counter[tuple[str, str]]
    sequences: int = 0

    @classmethod
    def empty(cls) -> "ModelCounts":
        return cls(
            Counter(),
            defaultdict(Counter),
            defaultdict(Counter),
            defaultdict(Counter),
            defaultdict(Counter),
            defaultdict(Counter),
            Counter(),
            defaultdict(Counter),
            defaultdict(Counter),
            defaultdict(Counter),
            defaultdict(set),
            Counter(),
            Counter(),
            Counter(),
            Counter(),
            Counter(),
            Counter(),
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


def bmes_for_word(word: str) -> list[str]:
    if len(word) == 1:
        return ["S"]
    if len(word) == 2:
        return ["B", "E"]
    return ["B", *("M" for _ in range(len(word) - 2)), "E"]


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: JSON 格式錯誤") from error
            if row.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(f"{path}:{line_number}: schema_version 不相容")
            yield row


def annotation_shards(output_dir: Path) -> list[Path]:
    return sorted((output_dir / "annotations").glob("*/batch-*.jsonl"))


def load_retokenization_evidence(model_dir: Path) -> RetokenizationEvidence | None:
    database_path = model_dir.parent / "word-counts.sqlite3"
    if database_path.is_file():
        evidence = RetokenizationEvidence.empty(source=str(database_path))
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        try:
            for surface, label, count in connection.execute(
                "SELECT word, entity, count FROM word_entity"
            ):
                evidence.entity_counts[(str(surface), str(label))] += int(count)
            placeholders = ",".join("?" for _ in NOUN_SUFFIX_MORPHEMES)
            query = (
                "SELECT word, SUM(count) FROM word_pos "
                f"WHERE substr(word, -1, 1) IN ({placeholders}) GROUP BY word"
            )
            for surface, support in connection.execute(
                query, tuple(sorted(NOUN_SUFFIX_MORPHEMES))
            ):
                evidence.whole_word_counts[str(surface)] += int(support)
        finally:
            connection.close()
        return evidence

    entities_path = model_dir / "NamedEntities.jsonl"
    evidence_path = model_dir / "Dict.evidence.jsonl"
    if not entities_path.is_file() or not evidence_path.is_file():
        return None
    evidence = RetokenizationEvidence.empty(source=str(model_dir))
    with entities_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            surface = str(row.get("word", ""))
            for label, count in row.get("entity_counts", {}).items():
                evidence.entity_counts[(surface, str(label))] += int(count)
    with evidence_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            surface = str(row.get("word", ""))
            if surface and surface[-1] in NOUN_SUFFIX_MORPHEMES:
                evidence.whole_word_counts[surface] += int(row.get("support", 0))
    return evidence


def open_count_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA temp_store = MEMORY;
        CREATE TABLE word_pos (
            word TEXT NOT NULL,
            pos TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (word, pos)
        ) WITHOUT ROWID;
        CREATE TABLE word_entity (
            word TEXT NOT NULL,
            entity TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (word, entity)
        ) WITHOUT ROWID;
        """
    )
    return connection


def upsert_counter(
    connection: sqlite3.Connection,
    table: str,
    key_column: str,
    counter: Mapping[tuple[str, str], int],
) -> None:
    if not counter:
        return
    connection.executemany(
        f"""
        INSERT INTO {table} (word, {key_column}, count) VALUES (?, ?, ?)
        ON CONFLICT(word, {key_column}) DO UPDATE SET count = count + excluded.count
        """,
        ((word, key, count) for (word, key), count in counter.items()),
    )


def add_sequence(counts: ModelCounts, sequence: Sequence[tuple[str, str]]) -> None:
    if not sequence:
        return
    states: list[str] = []
    joint: list[str] = []
    chars: list[str] = []
    for word, tag in sequence:
        word_states = bmes_for_word(word)
        states.extend(word_states)
        joint.extend(f"{state}-{tag}" for state in word_states)
        chars.extend(word)
    counts.sequences += 1
    counts.bmes_start[states[0]] += 1
    counts.pos_start[joint[0]] += 1
    for index, (char, state, pos_state) in enumerate(zip(chars, states, joint)):
        counts.bmes_emit1[state][char] += 1
        counts.bmes_reverse[char][state] += 1
        counts.pos_emit[pos_state][char] += 1
        counts.char_states[char].add(pos_state)
        if index >= 1:
            previous = states[index - 1]
            previous_pos = joint[index - 1]
            counts.bmes_trans1[previous][state] += 1
            counts.bmes_emit2[(previous, state)][char] += 1
            counts.pos_trans1[previous_pos][pos_state] += 1
        if index >= 2:
            counts.bmes_trans2[(states[index - 2], states[index - 1])][state] += 1
            counts.pos_trans2[(joint[index - 2], joint[index - 1])][pos_state] += 1


def collect_retokenization_policy(
    shards: Sequence[Path],
    *,
    min_entity_support: int,
    min_entity_confidence: float,
    min_morpheme_support: int,
) -> tuple[RetokenizationPolicy, RetokenizationEvidence]:
    evidence = RetokenizationEvidence.empty()
    for shard_number, shard in enumerate(shards, 1):
        for record in iter_jsonl(shard):
            add_record_evidence(record, evidence)
        if shard_number % 100 == 0 or shard_number == len(shards):
            print(
                f"[policy] 已讀取 {shard_number:,}/{len(shards):,} shards",
                flush=True,
            )
    return (
        build_policy(
            evidence,
            min_entity_support=min_entity_support,
            min_entity_confidence=min_entity_confidence,
            min_morpheme_support=min_morpheme_support,
        ),
        evidence,
    )


def consume_record(
    record: Mapping[str, Any],
    counts: ModelCounts,
    word_pos: Counter[tuple[str, str]],
    word_entity: Counter[tuple[str, str]],
    policy: RetokenizationPolicy,
) -> None:
    source = str(record.get("source", "unknown"))
    counts.source_records[source] += 1
    original_token_count = len(record.get("tokens", ()))
    canonical_tokens, valid_spans, invalid_spans = canonicalize_record(record, policy)
    for reason, count in invalid_spans.items():
        counts.retokenization[f"invalid:{reason}"] += count
    counts.source_original_tokens[source] += original_token_count
    counts.source_tokens[source] += len(canonical_tokens)
    counts.retokenization["original_tokens"] += original_token_count
    counts.retokenization["canonical_tokens"] += len(canonical_tokens)
    counts.retokenization["tokens_removed"] += max(
        0, original_token_count - len(canonical_tokens)
    )

    sequence: list[tuple[str, str]] = []
    for token in canonical_tokens:
        counts.retokenization[f"token:{token.reason}"] += 1
        word = token.text
        tag = token.pos
        if isinstance(tag, str) and tag and is_han_word(word):
            if token.dictionary_eligible:
                word_pos[(word, tag)] += 1
            sequence.append((word, tag))
        else:
            if (
                token.dictionary_eligible
                and isinstance(tag, str)
                and tag
                and token.reason == "named-entity"
                and is_dictionary_surface(word)
            ):
                word_pos[(word, tag)] += 1
            add_sequence(counts, sequence)
            sequence = []

        if (
            token.reason == "quantity"
            and token.canonical
            and token.label in QUANTITY_ENTITY_LABELS
        ):
            counts.quantity_patterns[(token.canonical, token.label)] += 1
            if isinstance(tag, str) and tag and is_dictionary_surface(token.canonical):
                word_pos[(token.canonical, tag)] += 1
    add_sequence(counts, sequence)

    for entity in record.get("entities", ()):
        counts.entity_labels[str(entity.get("label", ""))] += 1
    for span in valid_spans:
        if span.label in CKIP_ENTITY_LABELS and is_dictionary_surface(span.text):
            word_entity[(span.text, span.label)] += 1


def collect_counts(
    shards: Sequence[Path], database_path: Path, policy: RetokenizationPolicy
) -> tuple[ModelCounts, sqlite3.Connection]:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(database_path) + suffix)
        if candidate.exists():
            candidate.unlink()
    counts = ModelCounts.empty()
    connection = open_count_database(database_path)
    try:
        for shard_number, shard in enumerate(shards, 1):
            word_pos: Counter[tuple[str, str]] = Counter()
            word_entity: Counter[tuple[str, str]] = Counter()
            for record in iter_jsonl(shard):
                consume_record(record, counts, word_pos, word_entity, policy)
            with connection:
                upsert_counter(connection, "word_pos", "pos", word_pos)
                upsert_counter(connection, "word_entity", "entity", word_entity)
            if shard_number % 100 == 0 or shard_number == len(shards):
                print(f"[build] 已讀取 {shard_number:,}/{len(shards):,} shards", flush=True)
        return counts, connection
    except Exception:
        connection.close()
        raise


def grouped_rows(rows: Iterable[Sequence[Any]]) -> Iterator[tuple[str, dict[str, int]]]:
    current: str | None = None
    values: dict[str, int] = {}
    for raw_word, raw_key, raw_count in rows:
        word = str(raw_word)
        if current is not None and word != current:
            yield current, values
            values = {}
        current = word
        values[str(raw_key)] = int(raw_count)
    if current is not None:
        yield current, values


def merge_evidence(
    pos_groups: Iterable[tuple[str, dict[str, int]]],
    entity_groups: Iterable[tuple[str, dict[str, int]]],
) -> Iterator[tuple[str, dict[str, int], dict[str, int]]]:
    pos_iter, entity_iter = iter(pos_groups), iter(entity_groups)
    pos_item = next(pos_iter, None)
    entity_item = next(entity_iter, None)
    while pos_item is not None or entity_item is not None:
        pos_word = pos_item[0] if pos_item else None
        entity_word = entity_item[0] if entity_item else None
        if entity_word is None or (pos_word is not None and pos_word < entity_word):
            assert pos_item is not None
            yield pos_item[0], pos_item[1], {}
            pos_item = next(pos_iter, None)
        elif pos_word is None or entity_word < pos_word:
            assert entity_item is not None
            yield entity_item[0], {}, entity_item[1]
            entity_item = next(entity_iter, None)
        else:
            assert pos_item is not None and entity_item is not None
            yield pos_item[0], pos_item[1], entity_item[1]
            pos_item = next(pos_iter, None)
            entity_item = next(entity_iter, None)


def dictionary_reason(
    support: int,
    confidence: float,
    margin: float,
    entity_support: int,
    entity_confidence: float,
    *,
    min_support: int,
    min_pos_confidence: float,
    min_pos_margin: float,
    min_entity_support: int,
    min_entity_confidence: float,
) -> tuple[bool, str]:
    if support < min_support:
        return False, "insufficient-support"
    general_ok = confidence >= min_pos_confidence and margin >= min_pos_margin
    entity_ok = (
        entity_support >= min_entity_support
        and entity_confidence >= min_entity_confidence
        and confidence >= min_pos_confidence
    )
    if general_ok:
        return True, "accepted-general"
    if entity_ok:
        return True, "accepted-entity"
    if confidence < min_pos_confidence:
        return False, "ambiguous-pos"
    if margin < min_pos_margin and entity_support < min_entity_support:
        return False, "small-pos-margin"
    return False, "ambiguous-entity"


def write_dictionary(
    connection: sqlite3.Connection,
    output_dir: Path,
    *,
    min_support: int,
    min_pos_confidence: float,
    min_pos_margin: float,
    min_entity_support: int,
    min_entity_confidence: float,
) -> dict[str, int]:
    pos_groups = grouped_rows(
        connection.execute("SELECT word, pos, count FROM word_pos ORDER BY word, pos")
    )
    entity_groups = grouped_rows(
        connection.execute("SELECT word, entity, count FROM word_entity ORDER BY word, entity")
    )
    accepted_count = review_count = evidence_count = 0
    first_item = True
    with (
        (output_dir / "Dict.json").open("w", encoding="utf-8", newline="\n") as dictionary,
        (output_dir / "Dict.evidence.jsonl").open("w", encoding="utf-8", newline="\n") as evidence,
        (output_dir / "Dict.review.jsonl").open("w", encoding="utf-8", newline="\n") as review,
        (output_dir / "NamedEntities.jsonl").open("w", encoding="utf-8", newline="\n") as entities,
    ):
        dictionary.write("{\n")
        for word, pos_counts, entity_counts in merge_evidence(pos_groups, entity_groups):
            if not pos_counts:
                inferred: Counter[str] = Counter()
                for label, count in entity_counts.items():
                    if label in CKIP_ENTITY_DEFAULT_POS:
                        inferred[CKIP_ENTITY_DEFAULT_POS[label]] += count
                pos_counts = dict(inferred)
            if not pos_counts:
                continue
            ranked_pos = sorted(pos_counts.items(), key=lambda row: (-row[1], row[0]))
            primary_pos, primary_count = ranked_pos[0]
            support = sum(pos_counts.values())
            second_count = ranked_pos[1][1] if len(ranked_pos) > 1 else 0
            confidence = primary_count / support
            margin = (primary_count - second_count) / support
            ranked_entities = sorted(entity_counts.items(), key=lambda row: (-row[1], row[0]))
            entity_support = sum(entity_counts.values())
            entity_confidence = ranked_entities[0][1] / entity_support if entity_support else 0.0
            accepted, reason = dictionary_reason(
                support,
                confidence,
                margin,
                entity_support,
                entity_confidence,
                min_support=min_support,
                min_pos_confidence=min_pos_confidence,
                min_pos_margin=min_pos_margin,
                min_entity_support=min_entity_support,
                min_entity_confidence=min_entity_confidence,
            )
            row = {
                "word": word,
                "support": support,
                "pos_counts": dict(ranked_pos),
                "primary_pos": primary_pos,
                "pos_confidence": confidence,
                "pos_margin": margin,
                "entity_counts": dict(ranked_entities),
                "entity_support": entity_support,
                "entity_confidence": entity_confidence,
                "accepted": accepted,
                "reason": reason,
            }
            encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            evidence.write(encoded + "\n")
            evidence_count += 1
            if entity_counts:
                entities.write(
                    json.dumps(
                        {"word": word, "support": entity_support, "entity_counts": dict(ranked_entities)},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            if accepted and len(word) >= 2:
                if not first_item:
                    dictionary.write(",\n")
                dictionary.write(
                    "  " + json.dumps(word, ensure_ascii=False) + ": "
                    + json.dumps([primary_pos, support], ensure_ascii=False)
                )
                first_item = False
                accepted_count += 1
            else:
                review.write(encoded + "\n")
                review_count += 1
        dictionary.write("\n}\n")
    return {
        "dictionary_evidence": evidence_count,
        "dictionary_entries": accepted_count,
        "dictionary_review_queue": review_count,
    }


def log_row(counter: Mapping[str, int], allowed: Sequence[str], alpha: float) -> dict[str, float]:
    total = sum(counter.get(state, 0) for state in allowed) + alpha * len(allowed)
    if total <= 0:
        return {state: MIN_LOG for state in allowed}
    return {
        state: math.log((counter.get(state, 0) + alpha) / total)
        for state in allowed
    }


def log_emission_row(
    counter: Mapping[str, int], vocabulary: Sequence[str], alpha: float
) -> dict[str, float]:
    """Additive smoothing over the global character vocabulary plus UNK."""
    denominator = sum(counter.values()) + alpha * (len(vocabulary) + 1)
    if denominator <= 0:
        return {"<UNK>": MIN_LOG}
    row = {
        char: math.log((count + alpha) / denominator)
        for char, count in counter.items()
    }
    row["<UNK>"] = math.log(alpha / denominator)
    return row


def dump_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def legal_joint_next(previous: str, candidate: str) -> bool:
    previous_state, previous_tag = previous.split("-", 1)
    state, tag = candidate.split("-", 1)
    if previous_state in {"B", "M"}:
        return tag == previous_tag and state in {"M", "E"}
    return state in {"B", "S"}


def write_quantity_patterns(counts: ModelCounts, output_dir: Path) -> int:
    path = output_dir / "QuantityPatterns.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for (canonical, label), support in sorted(
            counts.quantity_patterns.items(), key=lambda row: (row[0][1], row[0][0])
        ):
            handle.write(
                json.dumps(
                    {"canonical": canonical, "label": label, "support": support},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return len(counts.quantity_patterns)


def write_bmes_model(counts: ModelCounts, output_dir: Path, alpha: float) -> None:
    start = {state: MIN_LOG for state in BMES}
    start.update(log_row(counts.bmes_start, ("B", "S"), alpha))
    trans1: dict[str, dict[str, float]] = {}
    trans2: dict[str, dict[str, dict[str, float]]] = {}
    for previous in BMES:
        row = log_row(counts.bmes_trans1[previous], LEGAL_NEXT[previous], alpha)
        trans1[previous] = {state: row.get(state, MIN_LOG) for state in BMES}
    for previous2 in BMES:
        trans2[previous2] = {}
        for previous1 in BMES:
            row = log_row(
                counts.bmes_trans2[(previous2, previous1)], LEGAL_NEXT[previous1], alpha
            )
            trans2[previous2][previous1] = {
                state: row.get(state, MIN_LOG) for state in BMES
            }
    vocabulary = sorted(counts.bmes_reverse)
    emit1 = {
        state: log_emission_row(counter, vocabulary, alpha)
        for state, counter in sorted(counts.bmes_emit1.items())
    }
    emit2 = {
        previous: {
            current: log_emission_row(
                counts.bmes_emit2[(previous, current)],
                vocabulary,
                alpha,
            )
            for current in BMES
            if counts.bmes_emit2[(previous, current)]
        }
        for previous in BMES
    }


    dump_json(output_dir / "startProbs.json", start)
    dump_json(output_dir / "transProbs.json", trans1)
    dump_json(output_dir / "transProbs2.json", trans2)
    dump_json(output_dir / "emmitProbs.json", emit1)
    dump_json(output_dir / "emmitProbs2.json", emit2)

def write_pos_model(counts: ModelCounts, output_dir: Path, alpha: float) -> int:
    observed_states = set(counts.pos_start) | set(counts.pos_emit) | set(counts.pos_trans1)
    tags = sorted({state.split("-", 1)[1] for state in observed_states})
    states = [f"{state}-{tag}" for tag in tags for state in BMES]
    allowed_start = [state for state in states if state.startswith(("B-", "S-"))]
    start = {state: MIN_LOG for state in states}
    start.update(log_row(counts.pos_start, allowed_start, alpha))
    trans1: dict[str, dict[str, float]] = {}
    trans2: dict[str, dict[str, dict[str, float]]] = {}
    legal_rows = {
        previous: [candidate for candidate in states if legal_joint_next(previous, candidate)]
        for previous in states
    }
    for previous in states:
        row = log_row(counts.pos_trans1[previous], legal_rows[previous], alpha)
        trans1[previous] = {
            candidate: row.get(candidate, MIN_LOG) for candidate in states
        }
    # Rust PosModel 目前只消費一階 tagTransProbs；二階檔供後續 runtime 升級。
    for previous2 in states:
        trans2[previous2] = {
            previous1: log_row(
                counts.pos_trans2[(previous2, previous1)], legal_rows[previous1], alpha
            )
            for previous1 in states
        }
    emit = {
        state: log_emission_row(counts.pos_emit[state], sorted(counts.char_states), alpha)
        for state in states
        if counts.pos_emit[state]
    }
    char_state_table = {
        char: sorted(states_for_char)
        for char, states_for_char in sorted(counts.char_states.items())
    }
    dump_json(output_dir / "tagStartProbs.json", start)
    dump_json(output_dir / "tagTransProbs.json", trans1)
    dump_json(output_dir / "tagTransProbs2.json", trans2)
    dump_json(output_dir / "tagEmitProbs.json", emit)
    dump_json(output_dir / "char_state_tab.json", char_state_table)
    return len(tags)


def write_pos_lexicon(connection: sqlite3.Connection, output_dir: Path) -> int:
    """Write every observed word (including one-character words) with full POS counts."""
    count = 0
    first = True
    with (output_dir / "PosLexicon.json").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("{\n")
        rows = connection.execute(
            "SELECT word, pos, count FROM word_pos WHERE count > 0 ORDER BY word, pos"
        )
        for word, tags in grouped_rows(rows):
            if not word or not tags:
                continue
            if not first:
                handle.write(",\n")
            handle.write(
                "  " + json.dumps(word, ensure_ascii=False) + ": "
                + json.dumps(tags, ensure_ascii=False, separators=(",", ":"))
            )
            first = False
            count += 1
        handle.write("\n}\n")
    return count

MODEL_FINGERPRINT_FILES = (
    "Dict.json", "VariantWords.json", "startProbs.json", "transProbs.json",
    "transProbs2.json", "emmitProbs.json", "emmitProbs2.json",
    "tagStartProbs.json", "tagTransProbs.json", "tagTransProbs2.json",
    "tagEmitProbs.json", "PosLexicon.json",
)


def write_model_fingerprint(model_dir: Path) -> str:
    """Stable FNV-1a fingerprint over the canonical runtime inputs."""
    value = 0xCBF29CE484222325
    for name in MODEL_FINGERPRINT_FILES:
        for byte in name.encode("utf-8") + b"\0" + (model_dir / name).read_bytes():
            value ^= byte
            value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    fingerprint = f"fnv1a64:{value:016x}"
    (model_dir / "model-fingerprint.txt").write_text(
        fingerprint + "\n", encoding="ascii"
    )
    return fingerprint
def build_models(
    output_dir: Path,
    *,
    model_output_dir: Path | None = None,
    policy_evidence_dir: Path | None = None,
    alpha: float = 0.1,
    min_support: int = 10,
    min_pos_confidence: float = 0.8,
    min_pos_margin: float = 0.2,
    min_entity_support: int = 2,
    min_entity_confidence: float = 0.8,
    min_morpheme_support: int = 2,
) -> dict[str, Any]:
    if alpha <= 0:
        raise ValueError("alpha 必須大於 0")
    shards = annotation_shards(output_dir)
    if not shards:
        raise FileNotFoundError(f"{output_dir / 'annotations'} 內沒有標註 shard")
    model_dir = model_output_dir or output_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = policy_evidence_dir or output_dir / "model"
    evidence = load_retokenization_evidence(evidence_dir)
    if evidence is None:
        policy, evidence = collect_retokenization_policy(
            shards,
            min_entity_support=min_entity_support,
            min_entity_confidence=min_entity_confidence,
            min_morpheme_support=min_morpheme_support,
        )
    else:
        print(f"[policy] 使用既有統計：{evidence_dir}", flush=True)
        policy = build_policy(
            evidence,
            min_entity_support=min_entity_support,
            min_entity_confidence=min_entity_confidence,
            min_morpheme_support=min_morpheme_support,
        )
    counts, connection = collect_counts(
        shards, model_dir.parent / "word-counts.sqlite3", policy
    )
    try:
        dictionary_report = write_dictionary(
            connection,
            model_dir,
            min_support=min_support,
            min_pos_confidence=min_pos_confidence,
            min_pos_margin=min_pos_margin,
            min_entity_support=min_entity_support,
            min_entity_confidence=min_entity_confidence,
        )
        pos_lexicon_count = write_pos_lexicon(connection, model_dir)
    finally:
        connection.close()
    write_bmes_model(counts, model_dir, alpha)
    tag_count = write_pos_model(counts, model_dir, alpha)
    quantity_pattern_count = write_quantity_patterns(counts, model_dir)
    dump_json(model_dir / "VariantWords.json", {})
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "gold_standard": False,
        "annotation_engine": "ckiptagger",
        "pos_tagset": "ckip",
        "tokenization_policy": policy.report(evidence),
        "annotation_warning": (
            "CKIP 自動標註是 silver data，不等同人工覆核 gold data；"
            "正式發布前應抽樣複核並使用獨立 test split 評測。"
        ),
        "annotation_shards": len(shards),
        "source_records": dict(sorted(counts.source_records.items())),
        "source_original_tokens": dict(sorted(counts.source_original_tokens.items())),
        "source_tokens": dict(sorted(counts.source_tokens.items())),
        "retokenization": dict(sorted(counts.retokenization.items())),
        "sequences": counts.sequences,
        "entity_labels": dict(sorted(counts.entity_labels.items())),
        "quantity_patterns": quantity_pattern_count,
        "pos_tag_count": tag_count,
        "pos_lexicon_entries": pos_lexicon_count,
        "smoothing_alpha": alpha,
        "dictionary_thresholds": {
            "min_support": min_support,
            "min_pos_confidence": min_pos_confidence,
            "min_pos_margin": min_pos_margin,
            "min_entity_support": min_entity_support,
            "min_entity_confidence": min_entity_confidence,
            "min_morpheme_support": min_morpheme_support,
        },
        **dictionary_report,
        "runtime_compatibility": {
            "bmes_order": 2,
            "pos_runtime_order": 2,
            "pos_order2_file_generated": True,
            "known_word_pos": "full P(tag|word) lexicon",
        },
    }
    dump_json(model_dir / "training-report.json", report)
    report["model_fingerprint"] = write_model_fingerprint(model_dir)
    dump_json(model_dir / "training-report.json", report)
    return report
