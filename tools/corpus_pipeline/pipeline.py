from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

SCHEMA_VERSION = 1
TAIWAN_TERMS = re.compile(
    r"(?:臺灣|台灣|臺北|台北|新北|桃園|臺中|台中|彰化|南投|雲林|嘉義|臺南|台南|"
    r"高雄|屏東|基隆|新竹|苗栗|宜蘭|花蓮|臺東|台東|澎湖|金門|馬祖)"
)
SENTENCE_RE = re.compile(r".*?(?:[。！？!?；;\n]+|$)", re.DOTALL)
PII_RE = re.compile(
    r"(?:[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|"
    r"(?<!\d)09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)|"
    r"(?<![A-Z0-9])[A-Z][12]\d{8}(?!\d))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceSpec:
    id: str
    adapter: str
    dataset: str
    config: str
    split: str
    revision: str
    license: str
    license_url: str
    target_count: int
    notes: str = ""


def load_manifest(path: Path) -> tuple[dict[str, Any], list[SourceSpec]]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported manifest schema: {data.get('schema_version')}")
    return data.get("workspace", {}), [SourceSpec(**row) for row in data["sources"]]


def stable_int(*parts: object) -> int:
    value = "\x1f".join(str(part) for part in parts)
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest(), "big")


def compact_metadata(row: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {
        key: row[key] for key in keys if key in row and row[key] not in (None, "", [])
    }


def adapt_row(
    spec: SourceSpec, row: Mapping[str, Any], row_index: int
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    if spec.adapter == "twllm_real_prompts":
        if row.get("dataset") != "v1log-geminipro":
            return
        conversations = row.get("conversations") or row.get("messages") or []
        for message_index, message in enumerate(conversations):
            role = message.get("from", message.get("role"))
            if role not in {"human", "user"}:
                continue
            text = message.get("value", message.get("content", ""))
            if isinstance(text, str):
                yield (
                    f"{row_index}:{message_index}",
                    text,
                    {"subset": row.get("dataset"), "role": role},
                )
        return

    if spec.adapter == "tw_law_current":
        if str(row.get("abandon_note") or "").strip():
            return
        text = row.get("text", "")
        if isinstance(text, str):
            metadata = compact_metadata(
                row, ("name", "level", "modified_date", "api_updated_date")
            )
            yield str(row_index), text, metadata
        return

    if spec.adapter == "taiwan_patent_exam":
        base_metadata = compact_metadata(row, ("source", "answer"))
        for field in ("question", "A", "B", "C", "D"):
            text = row.get(field, "")
            if not isinstance(text, str) or not text.strip():
                continue
            metadata = dict(base_metadata)
            metadata["field"] = field
            # The shared document id keeps a question and all choices in one split.
            yield str(row_index), text, metadata
        return

    if spec.adapter == "wikinews_tw":
        title, text = row.get("title", ""), row.get("text", "")
        if not isinstance(text, str) or not TAIWAN_TERMS.search(f"{title}\n{text}"):
            return
        if "-{" in text or "}-" in text:
            return
        metadata = compact_metadata(row, ("id", "url", "title"))
        yield str(row.get("id", row_index)), text, metadata
        return

    raise ValueError(f"unknown source adapter: {spec.adapter}")


def iter_documents(spec: SourceSpec) -> Iterator[tuple[str, str, dict[str, Any]]]:
    try:
        from datasets import load_dataset
    except ImportError as error:
        raise RuntimeError(
            "prepare requires the optional 'datasets' package"
        ) from error
    dataset = load_dataset(
        spec.dataset,
        spec.config,
        split=spec.split,
        revision=spec.revision,
        streaming=True,
    )
    for row_index, row in enumerate(dataset):
        yield from adapt_row(spec, row, row_index)


def split_text(text: str, max_chars: int = 500) -> Iterator[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    for match in SENTENCE_RE.finditer(text):
        sentence = match.group(0).strip()
        while sentence:
            chunk, sentence = sentence[:max_chars], sentence[max_chars:]
            chunk = chunk.strip()
            if chunk:
                yield chunk


def dedupe_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", normalized).casefold()


def is_han(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x3134F
    )


def quality_rejection(
    text: str, min_chars: int = 4, min_han_ratio: float = 0.35
) -> str | None:
    if len(text) < min_chars:
        return "too_short"
    if "\ufffd" in text:
        return "replacement_character"
    if PII_RE.search(text):
        return "suspected_pii"
    lowered = text.casefold()
    if "```" in text or "<script" in lowered or "function(" in lowered:
        return "code_like"
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return "empty"
    if sum(is_han(char) for char in visible) / len(visible) < min_han_ratio:
        return "low_han_ratio"
    return None


def assign_split(seed: int, source_id: str, document_id: str) -> str:
    bucket = stable_int(seed, source_id, document_id) % 1000
    if bucket < 980:
        return "train"
    if bucket < 990:
        return "dev"
    return "test"


def make_record(
    spec: SourceSpec,
    seed: int,
    document_id: str,
    segment_index: int,
    text: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    record_id = hashlib.sha256(
        f"{spec.id}\x1f{document_id}\x1f{segment_index}\x1f{text}".encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schema_version": SCHEMA_VERSION,
        "id": record_id,
        "source": {
            "id": spec.id,
            "dataset": spec.dataset,
            "revision": spec.revision,
            "license": spec.license,
            "license_url": spec.license_url,
        },
        "document_id": document_id,
        "segment_index": segment_index,
        "split": assign_split(seed, spec.id, document_id),
        "text": text,
        "metadata": dict(metadata),
        "preannotation": None,
        "review": {"status": "pending", "tokens": None, "annotator": None, "notes": ""},
    }


def select_records(
    specs: Sequence[SourceSpec],
    seed: int,
    max_chars: int = 500,
    count_override: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    report: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "sources": {}}
    for spec in specs:
        limit = count_override if count_override is not None else spec.target_count
        heap: list[tuple[int, int, dict[str, Any]]] = []
        counters: Counter[str] = Counter()
        serial = 0
        for document_id, raw_text, metadata in iter_documents(spec):
            counters["documents_seen"] += 1
            for segment_index, text in enumerate(
                split_text(raw_text, max_chars=max_chars)
            ):
                counters["segments_seen"] += 1
                rejection = quality_rejection(text)
                if rejection:
                    counters[f"rejected_{rejection}"] += 1
                    continue
                key = dedupe_key(text)
                if key in seen:
                    counters["rejected_duplicate"] += 1
                    continue
                seen.add(key)
                record = make_record(
                    spec, seed, document_id, segment_index, text, metadata
                )
                score = stable_int(seed, spec.id, record["id"])
                item = (-score, serial, record)
                serial += 1
                if len(heap) < limit:
                    heapq.heappush(heap, item)
                elif item > heap[0]:
                    heapq.heapreplace(heap, item)
                counters["eligible"] += 1
        source_records = [
            item[2] for item in sorted(heap, key=lambda item: (-item[0], item[1]))
        ]
        counters["selected"] = len(source_records)
        selected.extend(source_records)
        report["sources"][spec.id] = dict(counters)
    selected.sort(
        key=lambda record: (record["split"], record["source"]["id"], record["id"])
    )
    report["total_selected"] = len(selected)
    report["splits"] = dict(Counter(record["split"] for record in selected))
    return selected, report


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{line_number}: invalid JSON") from error


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def prepare_from_manifest(
    manifest_path: Path,
    output_path: Path,
    source_ids: set[str] | None = None,
    count_override: int | None = None,
    max_chars: int = 500,
) -> dict[str, Any]:
    workspace, specs = load_manifest(manifest_path)
    if source_ids:
        specs = [spec for spec in specs if spec.id in source_ids]
        missing = source_ids - {spec.id for spec in specs}
        if missing:
            raise ValueError(f"unknown source ids: {', '.join(sorted(missing))}")
    seed = int(workspace.get("seed", 20260728))
    hf_home = Path(workspace.get("directory", ".corpus-work")) / "hf"
    os.environ.setdefault("HF_HOME", str(hf_home.resolve()))
    records, report = select_records(
        specs, seed, max_chars=max_chars, count_override=count_override
    )
    write_jsonl(output_path, records)
    report_path = output_path.with_suffix(output_path.suffix + ".report.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
