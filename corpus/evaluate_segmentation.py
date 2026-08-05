"""Evaluate LingXi assets and jieba on Trident's Chinese test split.

The Trident example dataset stores word-segmentation gold labels as one BMES
string per sentence in ``ds.testdata.label.items[0].items``.  This script
restores the gold words, runs both LingXi asset sets through the release CLI,
and reports micro word/boundary scores plus end-to-end timing.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI = REPO_ROOT / "target" / "release" / "lingxi.exe"
DEFAULT_NEW_ASSETS = REPO_ROOT / "assets"
DEFAULT_OLD_ASSETS = (
    REPO_ROOT
    / ".corpus-work"
    / "model-archive"
    / "pre-full-ckip-20260801"
)
DEFAULT_OUTPUT = (
    REPO_ROOT
    / ".corpus-work"
    / "model-evaluation"
    / "trident-test-segmentation.json"
)
STATS_RE = re.compile(
    r"載入\s+(?P<load>\d+)\s+ms;\s+處理\s+(?P<mb>[0-9.]+)\s+MB\s+/\s+"
    r"(?P<seconds>[0-9.]+)\s+s\s+=\s+(?P<throughput>[0-9.]+)\s+MB/s"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare new/old LingXi assets and jieba on ds.testdata."
    )
    parser.add_argument("--cli", type=Path, default=DEFAULT_CLI)
    parser.add_argument("--new-assets", type=Path, default=DEFAULT_NEW_ASSETS)
    parser.add_argument("--old-assets", type=Path, default=DEFAULT_OLD_ASSETS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--limit", type=int, default=1400, help="固定診斷句數；0 表示全量")
    parser.add_argument("--skip-old", action="store_true", help="LXA2 不載入舊 LXA1 時略過舊資產")
    return parser.parse_args()


def words_from_bmes(sentence: str, labels: str) -> list[str]:
    if len(sentence) != len(labels):
        raise ValueError(
            f"sentence/label length mismatch: {len(sentence)} != {len(labels)}"
        )
    if any(label not in "BMES" for label in labels):
        raise ValueError("BMES labels contain an unsupported state")
    words: list[str] = []
    start = 0
    for index, label in enumerate(labels, 1):
        if label in "ES":
            words.append(sentence[start:index])
            start = index
    if start != len(sentence):
        raise ValueError("BMES labels do not terminate the final token")
    if "".join(words) != sentence:
        raise ValueError("restored gold words do not reconstruct the sentence")
    return words


def token_spans(tokens: Sequence[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        if not token:
            raise ValueError("empty token")
        end = cursor + len(token)
        spans.append((cursor, end))
        cursor = end
    return spans


def divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def harmonic_mean(precision: float, recall: float) -> float:
    return (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )


def evaluate(
    sentences: Sequence[str],
    gold: Sequence[Sequence[str]],
    predictions: Sequence[Sequence[str]],
) -> dict[str, Any]:
    if not (len(sentences) == len(gold) == len(predictions)):
        raise ValueError("sentence/gold/prediction counts differ")
    gold_words = predicted_words = matched_words = 0
    gold_boundaries = predicted_boundaries = matched_boundaries = 0
    exact_sentences = coverage_failures = 0
    for sentence, expected, actual in zip(sentences, gold, predictions):
        if "".join(actual) != sentence:
            coverage_failures += 1
            continue
        expected_spans = set(token_spans(expected))
        actual_spans = set(token_spans(actual))
        gold_words += len(expected_spans)
        predicted_words += len(actual_spans)
        matched_words += len(expected_spans & actual_spans)
        expected_boundaries = {end for _, end in expected_spans if end != len(sentence)}
        actual_boundaries = {end for _, end in actual_spans if end != len(sentence)}
        gold_boundaries += len(expected_boundaries)
        predicted_boundaries += len(actual_boundaries)
        matched_boundaries += len(expected_boundaries & actual_boundaries)
        exact_sentences += list(expected) == list(actual)
    word_precision = divide(matched_words, predicted_words)
    word_recall = divide(matched_words, gold_words)
    boundary_precision = divide(matched_boundaries, predicted_boundaries)
    boundary_recall = divide(matched_boundaries, gold_boundaries)
    return {
        "word_precision": word_precision,
        "word_recall": word_recall,
        "word_f1": harmonic_mean(word_precision, word_recall),
        "boundary_precision": boundary_precision,
        "boundary_recall": boundary_recall,
        "boundary_f1": harmonic_mean(boundary_precision, boundary_recall),
        "sentence_exact_match": divide(exact_sentences, len(sentences)),
        "exact_sentences": exact_sentences,
        "coverage_failures": coverage_failures,
        "matched_words": matched_words,
        "gold_words": gold_words,
        "predicted_words": predicted_words,
    }


def lingxi_once(
    cli: Path, assets: Path, sentences: Sequence[str]
) -> tuple[list[list[str]], dict[str, float]]:
    if any("\n" in sentence or "\r" in sentence for sentence in sentences):
        raise ValueError("LingXi line protocol cannot represent embedded newlines")
    payload = "\n".join(sentences) + "\n"
    started = time.perf_counter()
    process = subprocess.run(
        [
            str(cli),
            "--assets",
            str(assets),
            "--format",
            "jsonl",
            "--stats",
        ],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    wall_seconds = time.perf_counter() - started
    if process.returncode:
        raise RuntimeError(
            f"LingXi exited {process.returncode}: {process.stderr.strip()}"
        )
    rows = [json.loads(line) for line in process.stdout.splitlines()]
    if len(rows) != len(sentences):
        raise ValueError(f"LingXi returned {len(rows)} rows for {len(sentences)} sentences")
    predictions = [[str(token["w"]) for token in row["tokens"]] for row in rows]
    match = STATS_RE.search(process.stderr)
    if not match:
        raise ValueError(f"cannot parse LingXi stats: {process.stderr!r}")
    return predictions, {
        "wall_seconds": wall_seconds,
        "load_seconds": int(match.group("load")) / 1000,
        "processing_seconds": float(match.group("seconds")),
        "reported_mb": float(match.group("mb")),
        "reported_mb_per_second": float(match.group("throughput")),
    }


def median_speed(runs: Sequence[dict[str, float]], sentences: int) -> dict[str, Any]:
    result = {
        key: statistics.median(run[key] for run in runs)
        for key in runs[0]
    }
    result["sentences_per_second"] = divide(
        sentences, result["processing_seconds"]
    )
    result["repeats"] = len(runs)
    return result


def run_lingxi(
    cli: Path, assets: Path, sentences: Sequence[str], repeats: int
) -> tuple[list[list[str]], dict[str, Any]]:
    predictions: list[list[str]] | None = None
    timings: list[dict[str, float]] = []
    for _ in range(repeats):
        current, timing = lingxi_once(cli, assets, sentences)
        predictions = predictions or current
        timings.append(timing)
    assert predictions is not None
    return predictions, median_speed(timings, len(sentences))


def run_jieba(
    sentences: Sequence[str], repeats: int
) -> tuple[list[list[str]], dict[str, Any]]:
    import jieba

    started = time.perf_counter()
    jieba.initialize()
    load_seconds = time.perf_counter() - started
    predictions: list[list[str]] | None = None
    timings: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        current = [list(jieba.cut(sentence, cut_all=False, HMM=True)) for sentence in sentences]
        elapsed = time.perf_counter() - started
        predictions = predictions or current
        timings.append(elapsed)
    assert predictions is not None
    processing_seconds = statistics.median(timings)
    return predictions, {
        "load_seconds": load_seconds,
        "processing_seconds": processing_seconds,
        "wall_seconds": load_seconds + processing_seconds,
        "sentences_per_second": divide(len(sentences), processing_seconds),
        "repeats": repeats,
    }


def asset_bytes(directory: Path) -> int:
    return sum(
        (directory / name).stat().st_size
        for name in ("dict.bin", "hmm_bmes.bin", "hmm_pos.bin")
    )


def main() -> int:
    args = parse_args()
    if args.repeats <= 0:
        raise SystemExit("--repeats must be positive")
    paths = (args.cli, args.new_assets) if args.skip_old else (args.cli, args.new_assets, args.old_assets)
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)

    from trident import load_examples_data

    dataset_started = time.perf_counter()
    ds = load_examples_data("chinese")
    dataset_load_seconds = time.perf_counter() - dataset_started
    sentences = list(ds.testdata.data.items)
    if args.limit > 0:
        sentences = sentences[: args.limit]
    bmes_labels = list(ds.testdata.label.items[0].items)
    if args.limit > 0:
        bmes_labels = bmes_labels[: args.limit]
    if len(sentences) != len(bmes_labels):
        raise ValueError("Trident test sentence/label counts differ")
    gold = [
        words_from_bmes(sentence, labels)
        for sentence, labels in zip(sentences, bmes_labels)
    ]

    new_predictions, new_speed = run_lingxi(
        args.cli.resolve(), args.new_assets.resolve(), sentences, args.repeats
    )
    if args.skip_old:
        old_predictions = old_speed = None
    else:
        old_predictions, old_speed = run_lingxi(
            args.cli.resolve(), args.old_assets.resolve(), sentences, args.repeats
        )
    jieba_predictions, jieba_speed = run_jieba(sentences, args.repeats)

    report = {
        "dataset": {
            "source": "trident.load_examples_data('chinese').testdata",
            "sentences": len(sentences),
            "characters": sum(map(len, sentences)),
            "gold_tokens": sum(map(len, gold)),
            "dataset_load_seconds_excluded_from_model_timing": dataset_load_seconds,
            "gold_label_path": "ds.testdata.label.items[0].items (BMES)",
        },
        "samples": [
            {
                "sentence": sentences[index],
                "bmes": bmes_labels[index],
                "gold_tokens": gold[index],
            }
            for index in range(min(3, len(sentences)))
        ],
        "models": {
            "lingxi_new": {
                "assets": str(args.new_assets.resolve()),
                "asset_bytes": asset_bytes(args.new_assets),
                "metrics": evaluate(sentences, gold, new_predictions),
                "speed": new_speed,
            },
            "lingxi_old": (
                {"skipped": "LXA1 is intentionally rejected by the 0.3.0 LXA2 runtime"}
                if args.skip_old
                else {
                    "assets": str(args.old_assets.resolve()),
                    "asset_bytes": asset_bytes(args.old_assets),
                    "metrics": evaluate(sentences, gold, old_predictions),
                    "speed": old_speed,
                }
            ),
            "jieba": {
                "version": getattr(__import__("jieba"), "__version__", "unknown"),
                "metrics": evaluate(sentences, gold, jieba_predictions),
                "speed": jieba_speed,
            },
        },
        "timing_note": (
            "LingXi uses the release CLI and includes JSONL input/output in processing time; "
            "jieba is timed in-process after explicit dictionary initialization. Dataset "
            "loading is excluded. Medians are reported."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
