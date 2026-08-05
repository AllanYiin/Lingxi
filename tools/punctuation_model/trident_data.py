from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from .data import extract_labeled_text
from .labels import PUNCTUATION_NAMES, QUOTE_NAMES

TRIDENT_SOURCE = "trident/chinese/traindata"
PREPARED_SCHEMA_VERSION = 1


def _stable_split(text: str) -> str:
    bucket = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big") % 10_000
    if bucket < 9_800:
        return "train"
    if bucket < 9_900:
        return "dev"
    return "test"


def prepare_trident(
    output: Path,
    *,
    max_records: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Freeze Trident's Chinese training strings into auditable punctuation JSONL."""
    if output.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    # Delayed import keeps normal model training independent of Trident's startup cost.
    from trident import load_examples_data

    dataset = load_examples_data("chinese")
    items = dataset.traindata.data.items
    split_counts: Counter[str] = Counter()
    punctuation_counts: Counter[int] = Counter()
    quote_counts: Counter[int] = Counter()
    rejected: Counter[str] = Counter()
    boundaries = 0
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for index, value in enumerate(items):
            if max_records is not None and index >= max_records:
                break
            text = unicodedata.normalize("NFC", str(value).strip())
            if not text:
                rejected["empty"] += 1
                continue
            try:
                example = extract_labeled_text({"text": text})
            except ValueError as error:
                rejected[str(error)] += 1
                continue
            split = _stable_split(text)
            split_counts[split] += 1
            punctuation_counts.update(example.punctuation)
            quote_counts.update(example.quotes)
            boundaries += len(example.punctuation)
            handle.write(
                json.dumps(
                    {
                        "schema_version": PREPARED_SCHEMA_VERSION,
                        "id": f"trident-train-{index}",
                        "source": TRIDENT_SOURCE,
                        "split": split,
                        "text": example.text,
                        "input": "".join(example.units),
                        "punctuation_ids": list(example.punctuation),
                        "quote_ids": list(example.quotes),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    temporary.replace(output)
    report = {
        "schema_version": PREPARED_SCHEMA_VERSION,
        "source": TRIDENT_SOURCE,
        "source_records": len(items),
        "max_records": max_records,
        "accepted_records": sum(split_counts.values()),
        "split_counts": dict(sorted(split_counts.items())),
        "rejected_records": sum(rejected.values()),
        "rejected_reasons": dict(rejected.most_common()),
        "boundaries": boundaries,
        "punctuation_labels": list(PUNCTUATION_NAMES),
        "quote_labels": list(QUOTE_NAMES),
        "punctuation_counts": {
            PUNCTUATION_NAMES[index]: punctuation_counts[index]
            for index in range(len(PUNCTUATION_NAMES))
        },
        "quote_counts": {
            QUOTE_NAMES[index]: quote_counts[index] for index in range(len(QUOTE_NAMES))
        },
        "output": str(output.resolve()),
    }
    report_path = output.with_suffix(output.suffix + ".report.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report
