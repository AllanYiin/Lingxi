"""全量 CKIP WS/POS/NER 標註，並建立 LingXi 詞典與 HMM 模型。

預設整合 Trident 中文範例、NewsData.txt 與 PttData.txt。完整語料超過
一千三百萬行，標註結果會按批次原子寫入 JSONL shard；重跑時自動跳過
已完成 shard，避免中途中斷後從頭開始。
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_MODEL_DIR = SCRIPT_DIR / "data"
DEFAULT_NEWS = Path(r"D:\PycharmProjects\LingXi\ModelingData2\NewsData.txt")
DEFAULT_PTT = Path(r"D:\PycharmProjects\LingXi\ModelingData2\PttData.txt")
DEFAULT_OUTPUT = REPO_ROOT / ".corpus-work" / "ckip"
DEFAULT_SOURCES = ("trident", "news", "ptt")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class SourceRecord:
    source: str
    index: int
    text: str


def normalize_entities(raw: Iterable[Sequence[Any]]) -> list[tuple[int, int, str, str]]:
    rows = [
        (int(start), int(end), str(label), str(text))
        for start, end, label, text in raw
    ]
    return sorted(rows, key=lambda row: (row[0], row[1], row[2], row[3]))


def token_entity_labels(
    text: str,
    words: Sequence[str],
    entities: Sequence[tuple[int, int, str, str]],
) -> list[set[str]]:
    """把 CKIP NER 的字元 offset 投影到每個 token。"""

    spans: list[tuple[int, int]] = []
    cursor = 0
    for word in words:
        position = text.find(word, cursor)
        if position < 0:
            # CKIP 可能省略空白；退化為串接 offset，不讓單筆中止整批。
            position = cursor
        end = position + len(word)
        spans.append((position, end))
        cursor = end
    return [
        {
            label
            for entity_start, entity_end, label, _ in entities
            if token_start < entity_end and entity_start < token_end
        }
        for token_start, token_end in spans
    ]


def annotate_batch(
    records: Sequence[SourceRecord],
    ws: Callable[..., Sequence[Sequence[str]]],
    pos: Callable[..., Sequence[Sequence[str]]],
    ner: Callable[..., Sequence[Iterable[Sequence[Any]]]],
) -> list[dict[str, Any]]:
    texts = [record.text for record in records]
    word_rows = ws(
        texts,
        sentence_segmentation=False,
        segment_delimiter_set=set(),
        character_normalization=False,
    )
    pos_rows = pos(
        word_rows,
        sentence_segmentation=False,
        segment_delimiter_set=set(),
        character_normalization=False,
    )
    entity_rows = ner(word_rows, pos_rows)
    if not (len(records) == len(word_rows) == len(pos_rows) == len(entity_rows)):
        raise ValueError("CKIPTagger 回傳批次長度與輸入不一致")

    output: list[dict[str, Any]] = []
    for record, words, raw_tags, raw_entities in zip(
        records, word_rows, pos_rows, entity_rows
    ):
        if len(words) != len(raw_tags):
            raise ValueError(f"{record.source}:{record.index} 分詞與詞性數量不一致")
        entities = normalize_entities(raw_entities)
        labels_per_token = token_entity_labels(record.text, words, entities)
        tokens = []
        for word, ckip_pos, labels in zip(words, raw_tags, labels_per_token):
            tokens.append(
                {
                    "text": word,
                    "ckip_pos": ckip_pos,
                    "entity_labels": sorted(labels),
                }
            )
        output.append(
            {
                "schema_version": SCHEMA_VERSION,
                "source": record.source,
                "source_index": record.index,
                "text": record.text,
                "tokens": tokens,
                "entities": [
                    {"start": a, "end": b, "label": label, "text": value}
                    for a, b, label, value in entities
                ],
            }
        )
    return output


def iter_text_file(path: Path, source: str) -> Iterator[SourceRecord]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for index, line in enumerate(handle):
            text = line.rstrip("\r\n").strip()
            if text:
                yield SourceRecord(source, index, text)


def iter_trident() -> Iterator[SourceRecord]:
    # build 階段不需要載入 Trident/PyTorch，所以延遲 import。
    from trident import load_examples_data

    dataset = load_examples_data("chinese")
    for index, value in enumerate(dataset.traindata.data.items):
        text = str(value).strip()
        if text:
            yield SourceRecord("trident", index, text)


def iter_source(source: str, news_path: Path, ptt_path: Path) -> Iterator[SourceRecord]:
    if source == "trident":
        yield from iter_trident()
    elif source == "news":
        yield from iter_text_file(news_path, source)
    elif source == "ptt":
        yield from iter_text_file(ptt_path, source)
    else:
        raise ValueError(f"未知資料來源：{source}")


def batched(records: Iterable[SourceRecord], size: int) -> Iterator[list[SourceRecord]]:
    batch: list[SourceRecord] = []
    for record in records:
        batch.append(record)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def atomic_write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def valid_shard(path: Path, expected_count: int) -> bool:
    if not path.is_file():
        return False
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("schema_version") != SCHEMA_VERSION:
                    return False
                count += 1
    except (OSError, json.JSONDecodeError):
        return False
    return count == expected_count


def ensure_run_config(
    output_dir: Path,
    *,
    sources: Sequence[str],
    batch_size: int,
    max_records_per_source: int | None,
    model_dir: Path,
    news_path: Path,
    ptt_path: Path,
) -> None:
    config = {
        "schema_version": SCHEMA_VERSION,
        "sources": list(sources),
        "batch_size": batch_size,
        "max_records_per_source": max_records_per_source,
        "model_dir": str(model_dir.resolve()),
        "news_path": str(news_path.resolve()),
        "ptt_path": str(ptt_path.resolve()),
        "sentence_segmentation": False,
        "character_normalization": False,
        "ner_enabled": True,
    }
    path = output_dir / "annotation-config.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old != config:
            raise RuntimeError(
                f"{path} 與本次參數不同；請沿用原參數或指定新的 --output-dir。"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def annotate_sources(
    output_dir: Path,
    model_dir: Path,
    sources: Sequence[str],
    news_path: Path,
    ptt_path: Path,
    batch_size: int,
    disable_cuda: bool,
    max_records_per_source: int | None = None,
) -> dict[str, int]:
    if batch_size <= 0:
        raise ValueError("batch_size 必須大於 0")
    if "news" in sources and not news_path.is_file():
        raise FileNotFoundError(news_path)
    if "ptt" in sources and not ptt_path.is_file():
        raise FileNotFoundError(ptt_path)
    ensure_run_config(
        output_dir,
        sources=sources,
        batch_size=batch_size,
        max_records_per_source=max_records_per_source,
        model_dir=model_dir,
        news_path=news_path,
        ptt_path=ptt_path,
    )
    try:
        from ckiptagger import NER, POS, WS
    except ImportError as error:
        raise RuntimeError("annotate 階段需要 ckiptagger") from error

    ws = WS(str(model_dir), disable_cuda=disable_cuda)
    pos = POS(str(model_dir), disable_cuda=disable_cuda)
    ner = NER(str(model_dir), disable_cuda=disable_cuda)
    counts: dict[str, int] = {}
    try:
        for source in sources:
            iterator: Iterable[SourceRecord] = iter_source(source, news_path, ptt_path)
            for batch_number, batch in enumerate(batched(iterator, batch_size)):
                if (
                    max_records_per_source is not None
                    and batch_number * batch_size >= max_records_per_source
                ):
                    break
                if max_records_per_source is not None:
                    remaining = max_records_per_source - batch_number * batch_size
                    batch = batch[:remaining]
                shard = output_dir / "annotations" / source / f"batch-{batch_number:08d}.jsonl"
                if valid_shard(shard, len(batch)):
                    key = f"{source}_resumed"
                    counts[key] = counts.get(key, 0) + len(batch)
                    continue
                annotated = annotate_batch(batch, ws, pos, ner)
                atomic_write_jsonl(shard, annotated)
                key = f"{source}_annotated"
                counts[key] = counts.get(key, 0) + len(batch)
                total = counts.get(key, 0) + counts.get(f"{source}_resumed", 0)
                print(f"[{source}] 已完成 {total:,} 筆；最新 shard={shard.name}", flush=True)
    finally:
        del ner, pos, ws
        gc.collect()
    return dict(sorted(counts.items()))


def parse_sources(value: str) -> tuple[str, ...]:
    sources = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = sorted(set(sources) - set(DEFAULT_SOURCES))
    if not sources or unknown:
        detail = ",".join(unknown) if unknown else "空值"
        raise argparse.ArgumentTypeError(f"資料來源無效：{detail}")
    return sources


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必須大於 0")
    return parsed


def unit_interval(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("必須介於 0 與 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CKIP 全量標註與 LingXi 詞典／HMM 建模")
    parser.add_argument("command", nargs="?", choices=("annotate", "build", "all"), default="all")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument(
        "--build-model-dir",
        type=Path,
        help="build 產物目錄；預設為 <output-dir>/model",
    )
    parser.add_argument(
        "--policy-evidence-dir",
        type=Path,
        help="沿用 NamedEntities/Dict.evidence 的政策證據；預設為 <output-dir>/model",
    )
    parser.add_argument("--news-path", type=Path, default=DEFAULT_NEWS)
    parser.add_argument("--ptt-path", type=Path, default=DEFAULT_PTT)
    parser.add_argument("--sources", type=parse_sources, default=DEFAULT_SOURCES)
    parser.add_argument("--batch-size", type=positive_int, default=128)
    parser.add_argument("--max-records-per-source", type=positive_int, help="只供 smoke test；不指定才是全量")
    parser.add_argument("--cuda", action="store_true", help="啟用 CUDA；預設使用 CPU")
    parser.add_argument("--alpha", type=float, default=0.1)
    parser.add_argument("--min-support", type=positive_int, default=10)
    parser.add_argument("--min-pos-confidence", type=unit_interval, default=0.8)
    parser.add_argument("--min-pos-margin", type=unit_interval, default=0.2)
    parser.add_argument("--min-entity-support", type=positive_int, default=2)
    parser.add_argument("--min-entity-confidence", type=unit_interval, default=0.8)
    parser.add_argument("--min-morpheme-support", type=positive_int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"annotate", "all"}:
        report = annotate_sources(
            args.output_dir,
            args.model_dir,
            args.sources,
            args.news_path,
            args.ptt_path,
            args.batch_size,
            disable_cuda=not args.cuda,
            max_records_per_source=args.max_records_per_source,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.command in {"build", "all"}:
        from corpus.model_training import build_models

        report = build_models(
            args.output_dir,
            model_output_dir=args.build_model_dir,
            policy_evidence_dir=args.policy_evidence_dir,
            alpha=args.alpha,
            min_support=args.min_support,
            min_pos_confidence=args.min_pos_confidence,
            min_pos_margin=args.min_pos_margin,
            min_entity_support=args.min_entity_support,
            min_entity_confidence=args.min_entity_confidence,
            min_morpheme_support=args.min_morpheme_support,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
