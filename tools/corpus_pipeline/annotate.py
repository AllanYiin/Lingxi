from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .pipeline import SCHEMA_VERSION, read_jsonl, write_jsonl

AnnotationBatch = Callable[
    [Sequence[str]], Sequence[tuple[Sequence[str], Sequence[str]]]
]


def load_tag_map(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported tag-map schema: {data.get('schema_version')}")
    return data


def map_ckip_tag(
    tag: str, mapping: Mapping[str, Any]
) -> tuple[str | None, str, list[str]]:
    if tag in mapping["exact"]:
        target = mapping["exact"][tag]
        return target, "exact", [target]
    if tag in mapping["broad"]:
        target = mapping["broad"][tag]
        return target, "broad", [target]
    if tag in mapping["ambiguous"]:
        return None, "ambiguous", list(mapping["ambiguous"][tag])
    if any(tag.startswith(prefix) for prefix in mapping.get("skip_prefixes", [])):
        return None, "skip", []
    return None, "unmapped", []


def align_tokens(
    text: str, words: Sequence[str], tags: Sequence[str]
) -> list[tuple[str, str]]:
    if len(words) != len(tags):
        raise ValueError("CKIPTagger returned different word/POS lengths")
    aligned: list[tuple[str, str]] = []
    cursor = 0
    for word, tag in zip(words, tags):
        if not word:
            continue
        position = text.find(word, cursor)
        if position < 0:
            raise ValueError(f"cannot align CKIP token {word!r} at offset {cursor}")
        if position > cursor:
            aligned.append((text[cursor:position], "WHITESPACE"))
        aligned.append((word, tag))
        cursor = position + len(word)
    if cursor < len(text):
        aligned.append((text[cursor:], "WHITESPACE"))
    if "".join(token for token, _ in aligned) != text:
        raise ValueError("aligned CKIP tokens do not reconstruct source text")
    return aligned


def make_preannotation(
    text: str,
    words: Sequence[str],
    tags: Sequence[str],
    mapping: Mapping[str, Any],
) -> dict[str, Any]:
    tokens = []
    for token, ckip_pos in align_tokens(text, words, tags):
        suggested, mapping_status, candidates = map_ckip_tag(ckip_pos, mapping)
        tokens.append(
            {
                "text": token,
                "ckip_pos": ckip_pos,
                "suggested_pos": suggested,
                "mapping": mapping_status,
                "candidates": candidates,
            }
        )
    return {
        "engine": "ckiptagger",
        "engine_confidence_available": False,
        "tokens": tokens,
    }


def annotate_records(
    records: list[dict[str, Any]],
    annotator: AnnotationBatch,
    mapping: Mapping[str, Any],
    include_splits: set[str] | None = None,
    batch_size: int = 32,
) -> dict[str, int]:
    include_splits = include_splits or {"train", "dev"}
    indices = [
        index
        for index, record in enumerate(records)
        if record.get("split") in include_splits and record.get("preannotation") is None
    ]
    counts = {"annotated": 0, "errors": 0, "skipped": len(records) - len(indices)}
    for offset in range(0, len(indices), batch_size):
        batch_indices = indices[offset : offset + batch_size]
        texts = [records[index]["text"] for index in batch_indices]
        outputs = annotator(texts)
        if len(outputs) != len(texts):
            raise ValueError("annotator returned a different batch size")
        for index, (words, tags) in zip(batch_indices, outputs):
            try:
                preannotation = make_preannotation(
                    records[index]["text"], words, tags, mapping
                )
                records[index]["preannotation"] = preannotation
                records[index]["review"]["tokens"] = [
                    {"text": token["text"], "pos": token["suggested_pos"]}
                    for token in preannotation["tokens"]
                ]
                counts["annotated"] += 1
            except ValueError as error:
                records[index]["preannotation"] = {
                    "engine": "ckiptagger",
                    "engine_confidence_available": False,
                    "tokens": None,
                    "error": str(error),
                }
                counts["errors"] += 1
    return counts


def ckip_annotator(model_dir: Path, disable_cuda: bool = True) -> AnnotationBatch:
    try:
        from ckiptagger import POS, WS
    except ImportError as error:
        raise RuntimeError(
            "preannotate requires the optional 'ckiptagger' package"
        ) from error
    ws = WS(str(model_dir), disable_cuda=disable_cuda)
    pos = POS(str(model_dir), disable_cuda=disable_cuda)

    def annotate(texts: Sequence[str]) -> Sequence[tuple[Sequence[str], Sequence[str]]]:
        words = ws(
            list(texts),
            sentence_segmentation=False,
            segment_delimiter_set=set(),
            character_normalization=False,
        )
        tags = pos(
            words,
            sentence_segmentation=False,
            segment_delimiter_set=set(),
            character_normalization=False,
        )
        return list(zip(words, tags))

    return annotate


def preannotate_jsonl(
    input_path: Path,
    output_path: Path,
    model_dir: Path,
    mapping_path: Path,
    include_splits: set[str] | None = None,
    batch_size: int = 32,
    disable_cuda: bool = True,
) -> dict[str, int]:
    records = list(read_jsonl(input_path))
    mapping = load_tag_map(mapping_path)
    counts = annotate_records(
        records,
        ckip_annotator(model_dir, disable_cuda=disable_cuda),
        mapping,
        include_splits=include_splits,
        batch_size=batch_size,
    )
    write_jsonl(output_path, records)
    return counts
