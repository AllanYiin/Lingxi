"""Compare jieba, LingXi 0.2.2, and LingXi 0.3.0 on fixed Trident data."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POS_GOLD = (
    REPO_ROOT / ".corpus-work" / "model-evaluation" / "trident-test-ckip-gold.jsonl"
)
DEFAULT_JSON = (
    REPO_ROOT
    / ".corpus-work"
    / "model-evaluation"
    / "jieba-lingxi-022-030-comparison.json"
)
DEFAULT_MARKDOWN = (
    REPO_ROOT
    / ".corpus-work"
    / "model-evaluation"
    / "jieba-lingxi-022-030-comparison.md"
)
SEPARATOR = "\u241f"
STATS_RE = re.compile(
    r"載入\s+(?P<load>\d+)\s+ms;\s+處理\s+(?P<mb>[0-9.]+)\s+MB\s+/\s+"
    r"(?P<seconds>[0-9.]+)\s+s\s+=\s+(?P<throughput>[0-9.]+)\s+MB/s"
)
COMMON_TAGS = {
    "a",
    "c",
    "d",
    "e",
    "m",
    "n",
    "nr",
    "ns",
    "nt",
    "nz",
    "p",
    "q",
    "r",
    "s",
    "t",
    "u",
    "uj",
    "v",
    "vn",
    "w",
    "x",
}
JIEBA_ALIASES = {
    "ad": "a",
    "ag": "a",
    "an": "a",
    "b": "a",
    "z": "a",
    "dg": "d",
    "df": "d",
    "f": "s",
    "mg": "m",
    "mq": "q",
    "ng": "n",
    "nrfg": "nr",
    "nrt": "nr",
    "rg": "r",
    "rr": "r",
    "rz": "r",
    "tg": "t",
    "ud": "u",
    "ug": "u",
    "ul": "u",
    "uv": "u",
    "uz": "u",
    "vd": "v",
    "vg": "v",
    "eng": "x",
    "g": "x",
    "h": "x",
    "i": "x",
    "j": "x",
    "k": "x",
    "l": "x",
    "o": "x",
    "y": "x",
    "yg": "x",
    "zg": "x",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1400)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--pos-gold", type=Path, default=DEFAULT_POS_GOLD)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def divide(numerator: int | float, denominator: int | float) -> float:
    return numerator / denominator if denominator else 0.0


def f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


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


def words_from_bmes(sentence: str, labels: str) -> list[str]:
    if len(sentence) != len(labels):
        raise ValueError("sentence and BMES label lengths differ")
    words: list[str] = []
    start = 0
    for index, label in enumerate(labels, 1):
        if label not in "BMES":
            raise ValueError(f"invalid BMES label: {label}")
        if label in "ES":
            words.append(sentence[start:index])
            start = index
    if start != len(sentence) or "".join(words) != sentence:
        raise ValueError("BMES labels do not reconstruct the sentence")
    return words


def segmentation_metrics(
    sentences: Sequence[str],
    gold: Sequence[Sequence[str]],
    predicted: Sequence[Sequence[str]],
) -> dict[str, Any]:
    gold_words = predicted_words = matched_words = 0
    gold_boundaries = predicted_boundaries = matched_boundaries = 0
    exact = coverage_failures = 0
    for sentence, expected, actual in zip(sentences, gold, predicted):
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
        exact += list(expected) == list(actual)
    word_precision = divide(matched_words, predicted_words)
    word_recall = divide(matched_words, gold_words)
    boundary_precision = divide(matched_boundaries, predicted_boundaries)
    boundary_recall = divide(matched_boundaries, gold_boundaries)
    return {
        "word_precision": word_precision,
        "word_recall": word_recall,
        "word_f1": f1(word_precision, word_recall),
        "boundary_precision": boundary_precision,
        "boundary_recall": boundary_recall,
        "boundary_f1": f1(boundary_precision, boundary_recall),
        "sentence_exact_match": divide(exact, len(sentences)),
        "exact_sentences": exact,
        "coverage_failures": coverage_failures,
        "gold_words": gold_words,
        "predicted_words": predicted_words,
        "matched_words": matched_words,
    }


def median_timing(
    runs: Sequence[dict[str, float]], sentence_count: int
) -> dict[str, Any]:
    result = {key: statistics.median(run[key] for run in runs) for key in runs[0]}
    result["sentences_per_second"] = divide(
        sentence_count, result["processing_seconds"]
    )
    result["repeats"] = len(runs)
    return result


def lingxi_once(
    cli: Path,
    assets: Path,
    sentences: Sequence[str],
    mode: str,
) -> tuple[Any, dict[str, float]]:
    payload = "\n".join(sentences) + "\n"
    command = [
        str(cli),
        "--assets",
        str(assets),
        "--format",
        "words" if mode == "segmentation" else "jsonl",
        "--stats",
    ]
    if mode == "segmentation":
        command.extend(["--sep", SEPARATOR])
    started = time.perf_counter()
    process = subprocess.run(
        command,
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    wall_seconds = time.perf_counter() - started
    if process.returncode:
        raise RuntimeError(
            f"{cli.name} exited {process.returncode}: {process.stderr.strip()}"
        )
    lines = process.stdout.splitlines()
    if len(lines) != len(sentences):
        raise ValueError(f"{cli.name} returned {len(lines)} rows")
    if mode == "segmentation":
        predictions = [line.split(SEPARATOR) if line else [] for line in lines]
    else:
        rows = [json.loads(line) for line in lines]
        predictions = [
            [
                {"word": str(token["w"]), "raw_tag": str(token["t"])}
                for token in row["tokens"]
            ]
            for row in rows
        ]
    match = STATS_RE.search(process.stderr)
    if not match:
        raise ValueError(f"cannot parse stats from {cli.name}: {process.stderr!r}")
    return predictions, {
        "wall_seconds": wall_seconds,
        "load_seconds": int(match.group("load")) / 1000,
        "processing_seconds": float(match.group("seconds")),
        "reported_mb": float(match.group("mb")),
        "reported_mb_per_second": float(match.group("throughput")),
    }


def run_lingxi(
    cli: Path,
    assets: Path,
    sentences: Sequence[str],
    mode: str,
    repeats: int,
) -> tuple[Any, dict[str, Any]]:
    lingxi_once(cli, assets, sentences[: min(10, len(sentences))], mode)
    predictions = None
    timings: list[dict[str, float]] = []
    for _ in range(repeats):
        current, timing = lingxi_once(cli, assets, sentences, mode)
        if predictions is None:
            predictions = current
        elif predictions != current:
            raise ValueError(f"{cli.name} produced non-deterministic {mode} output")
        timings.append(timing)
    return predictions, median_timing(timings, len(sentences))


def punctuation_only(word: str) -> bool:
    return bool(word) and all(
        unicodedata.category(character).startswith(("P", "S")) or character.isspace()
        for character in word
    )


def build_ckip_mapping(rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for token in row["tokens"]:
            counts[str(token["ckip_pos"])][str(token["pos"])] += 1
    return {
        raw: distribution.most_common(1)[0][0] for raw, distribution in counts.items()
    }


def map_tag(
    word: str,
    raw_tag: str,
    source: str,
    ckip_mapping: dict[str, str],
) -> str:
    if punctuation_only(word):
        return "w"
    if source.startswith("lingxi"):
        return ckip_mapping.get(raw_tag, raw_tag if raw_tag in COMMON_TAGS else "x")
    if raw_tag in COMMON_TAGS:
        return raw_tag
    return JIEBA_ALIASES.get(raw_tag, "x")


def pos_metrics(
    gold_rows: Sequence[dict[str, Any]],
    predictions: Sequence[Sequence[dict[str, str]]],
    source: str,
    ckip_mapping: dict[str, str],
) -> dict[str, Any]:
    gold_all = predicted_all = aligned_all = correct_all = native_correct_all = 0
    gold_lex = predicted_lex = aligned_lex = correct_lex = native_correct_lex = 0
    coverage_failures = 0
    for row, predicted_tokens in zip(gold_rows, predictions):
        sentence = str(row["text"])
        predicted_words = [token["word"] for token in predicted_tokens]
        if "".join(predicted_words) != sentence:
            coverage_failures += 1
            continue
        gold_words = [str(token["text"]) for token in row["tokens"]]
        gold_spans = token_spans(gold_words)
        predicted_spans = token_spans(predicted_words)
        gold_by_span = {
            span: {
                "common": str(token["pos"]),
                "native": str(token["ckip_pos"]),
                "lexical": str(token["pos"]) != "w",
            }
            for span, token in zip(gold_spans, row["tokens"])
        }
        predicted_by_span = {
            span: {
                "common": map_tag(
                    token["word"], token["raw_tag"], source, ckip_mapping
                ),
                "native": token["raw_tag"],
            }
            for span, token in zip(predicted_spans, predicted_tokens)
        }
        gold_all += len(gold_by_span)
        predicted_all += len(predicted_by_span)
        gold_lex += sum(item["lexical"] for item in gold_by_span.values())
        predicted_lex += sum(
            item["common"] != "w" for item in predicted_by_span.values()
        )
        for span, gold in gold_by_span.items():
            predicted = predicted_by_span.get(span)
            if predicted is None:
                continue
            aligned_all += 1
            correct_all += predicted["common"] == gold["common"]
            if source.startswith("lingxi"):
                native_correct_all += predicted["native"] == gold["native"]
            if gold["lexical"]:
                aligned_lex += 1
                correct_lex += predicted["common"] == gold["common"]
                if source.startswith("lingxi"):
                    native_correct_lex += predicted["native"] == gold["native"]
    common_precision_all = divide(correct_all, predicted_all)
    common_recall_all = divide(correct_all, gold_all)
    common_precision_lex = divide(correct_lex, predicted_lex)
    common_recall_lex = divide(correct_lex, gold_lex)
    result: dict[str, Any] = {
        "alignment_coverage_all": divide(aligned_all, gold_all),
        "alignment_coverage_lexical": divide(aligned_lex, gold_lex),
        "common_accuracy_on_aligned_all": divide(correct_all, aligned_all),
        "common_accuracy_on_aligned_lexical": divide(correct_lex, aligned_lex),
        "effective_common_accuracy_all": divide(correct_all, gold_all),
        "effective_common_accuracy_lexical": divide(correct_lex, gold_lex),
        "joint_common_precision_all": common_precision_all,
        "joint_common_recall_all": common_recall_all,
        "joint_common_f1_all": f1(common_precision_all, common_recall_all),
        "joint_common_precision_lexical": common_precision_lex,
        "joint_common_recall_lexical": common_recall_lex,
        "joint_common_f1_lexical": f1(common_precision_lex, common_recall_lex),
        "gold_tokens": gold_all,
        "gold_lexical_tokens": gold_lex,
        "predicted_tokens": predicted_all,
        "predicted_lexical_tokens": predicted_lex,
        "aligned_tokens": aligned_all,
        "aligned_lexical_tokens": aligned_lex,
        "coverage_failures": coverage_failures,
    }
    if source.startswith("lingxi"):
        result["native_ckip_accuracy_on_aligned_all"] = divide(
            native_correct_all, aligned_all
        )
        result["native_ckip_accuracy_on_aligned_lexical"] = divide(
            native_correct_lex, aligned_lex
        )
    else:
        result["native_ckip_accuracy_on_aligned_all"] = None
        result["native_ckip_accuracy_on_aligned_lexical"] = None
    return result


def run_jieba_segmentation(
    sentences: Sequence[str],
    repeats: int,
) -> tuple[list[list[str]], dict[str, Any], float]:
    import jieba

    started = time.perf_counter()
    jieba.initialize()
    load_seconds = time.perf_counter() - started
    list(jieba.cut(sentences[0], cut_all=False, HMM=True))
    predictions = None
    timings: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        current = [
            list(jieba.cut(sentence, cut_all=False, HMM=True)) for sentence in sentences
        ]
        timings.append(time.perf_counter() - started)
        if predictions is None:
            predictions = current
        elif predictions != current:
            raise ValueError("jieba produced non-deterministic segmentation")
    processing = statistics.median(timings)
    return (
        predictions,
        {
            "load_seconds": load_seconds,
            "processing_seconds": processing,
            "wall_seconds": load_seconds + processing,
            "sentences_per_second": divide(len(sentences), processing),
            "repeats": repeats,
        },
        load_seconds,
    )


def run_jieba_pos(
    sentences: Sequence[str],
    repeats: int,
    load_seconds: float,
) -> tuple[list[list[dict[str, str]]], dict[str, Any]]:
    import jieba.posseg as pseg

    payload = "\n".join(sentences)
    list(pseg.cut(sentences[0], HMM=True))
    predictions = None
    timings: list[float] = []
    for repeat in range(repeats):
        started = time.perf_counter()
        current: list[list[dict[str, str]]] = [[]]
        for pair in pseg.cut(payload, HMM=True):
            if pair.word == "\n":
                current.append([])
            else:
                current[-1].append({"word": pair.word, "raw_tag": pair.flag})
        elapsed = time.perf_counter() - started
        print(
            f"[jieba POS batch] repeat {repeat + 1}/{repeats}: {elapsed:.3f}s",
            flush=True,
        )
        if len(current) != len(sentences):
            raise ValueError(f"jieba POS batch returned {len(current)} sentence rows")
        if any(
            "".join(token["word"] for token in row) != sentence
            for row, sentence in zip(current, sentences)
        ):
            raise ValueError("jieba POS batch did not reconstruct every sentence")
        timings.append(elapsed)
        if predictions is None:
            predictions = current
        elif predictions != current:
            raise ValueError("jieba produced non-deterministic POS output")
    processing = statistics.median(timings)
    predicted_tokens = sum(len(row) for row in predictions)
    return predictions, {
        "load_seconds": load_seconds,
        "processing_seconds": processing,
        "wall_seconds": load_seconds + processing,
        "sentences_per_second": divide(len(sentences), processing),
        "tokens_per_second": divide(predicted_tokens, processing),
        "repeats": repeats,
    }


def add_token_throughput(
    speed: dict[str, Any], predictions: Sequence[Sequence[Any]]
) -> None:
    token_count = sum(len(row) for row in predictions)
    speed["predicted_tokens"] = token_count
    speed["tokens_per_second"] = divide(token_count, speed["processing_seconds"])


def asset_bytes(path: Path) -> int:
    return sum(
        (path / name).stat().st_size
        for name in ("dict.bin", "hmm_bmes.bin", "hmm_pos.bin")
    )


def percentage(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def number(value: float) -> str:
    return f"{value:,.1f}"


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# jieba、LingXi 0.2.2、LingXi 0.3.0 比較",
        "",
        f"資料集：固定前 {report['dataset']['sentences']:,} 句，"
        f"{report['dataset']['characters']:,} 字。",
        "",
        "## 分詞",
        "",
        "| 模型 | 處理時間中位數 | 句／秒 | Word P | Word R | Word F1 | Boundary F1 | 句完全一致 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    display = {
        "jieba": "jieba 0.42.1",
        "lingxi_0_2_2": "LingXi 0.2.2",
        "lingxi_0_3_0": "LingXi 0.3.0",
    }
    for key in ("jieba", "lingxi_0_2_2", "lingxi_0_3_0"):
        model = report["models"][key]
        metric = model["segmentation"]["metrics"]
        speed = model["segmentation"]["speed"]
        lines.append(
            f"| {display[key]} | {speed['processing_seconds'] * 1000:.2f} ms | "
            f"{number(speed['sentences_per_second'])} | "
            f"{percentage(metric['word_precision'])} | "
            f"{percentage(metric['word_recall'])} | "
            f"{percentage(metric['word_f1'])} | "
            f"{percentage(metric['boundary_f1'])} | "
            f"{percentage(metric['sentence_exact_match'])} |"
        )
    lines.extend(
        [
            "",
            "## POS（分詞＋POS 端到端）",
            "",
            "| 模型 | 處理時間中位數 | 句／秒 | token／秒 | 詞界對齊率 | 共同 POS 準確率 | CKIP 原生準確率 | 詞界＋POS F1 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key in ("jieba", "lingxi_0_2_2", "lingxi_0_3_0"):
        model = report["models"][key]
        metric = model["pos"]["metrics"]
        speed = model["pos"]["speed"]
        lines.append(
            f"| {display[key]} | {speed['processing_seconds'] * 1000:.2f} ms | "
            f"{number(speed['sentences_per_second'])} | "
            f"{number(speed['tokens_per_second'])} | "
            f"{percentage(metric['alignment_coverage_lexical'])} | "
            f"{percentage(metric['common_accuracy_on_aligned_lexical'])} | "
            f"{percentage(metric['native_ckip_accuracy_on_aligned_lexical'])} | "
            f"{percentage(metric['joint_common_f1_lexical'])} |"
        )
    lines.extend(
        [
            "",
            "POS 指標排除標點。共同 POS 準確率只在詞界完全對齊的 token 上計算；"
            "詞界＋POS F1 同時懲罰錯誤詞界與錯誤詞性。",
            "",
            "LingXi 速度為 release CLI 回報的模型處理時間，包含輸出序列化；"
            "jieba 為同一 Python 行程內的處理時間。模型與資料集載入不計入處理時間。",
            "",
            "POS gold 是 CKIPTagger silver annotation，並非人工覆核 gold。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if args.limit <= 0 or args.repeats <= 0:
        raise SystemExit("--limit and --repeats must be positive")

    lingxi_specs = {
        "lingxi_0_2_2": {
            "version": "0.2.2",
            "cli": REPO_ROOT
            / "dist"
            / "lingxi-0.2.2"
            / "cli-windows-x86_64"
            / "lingxi.exe",
            "assets": REPO_ROOT
            / "dist"
            / "lingxi-0.2.2"
            / "cli-windows-x86_64"
            / "assets",
        },
        "lingxi_0_3_0": {
            "version": "0.3.0",
            "cli": REPO_ROOT / "target" / "release" / "lingxi.exe",
            "assets": REPO_ROOT / "assets",
        },
    }
    for spec in lingxi_specs.values():
        if not spec["cli"].is_file() or not spec["assets"].is_dir():
            raise FileNotFoundError(spec)

    all_pos_rows = [
        json.loads(line)
        for line in args.pos_gold.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pos_rows = all_pos_rows[: args.limit]
    ckip_mapping = build_ckip_mapping(all_pos_rows)

    from trident import load_examples_data

    dataset_started = time.perf_counter()
    dataset = load_examples_data("chinese")
    dataset_load_seconds = time.perf_counter() - dataset_started
    sentences = list(dataset.testdata.data.items)[: args.limit]
    labels = list(dataset.testdata.label.items[0].items)[: args.limit]
    official_gold = [
        words_from_bmes(sentence, label) for sentence, label in zip(sentences, labels)
    ]
    if len(pos_rows) != len(sentences):
        raise ValueError("POS gold and Trident sentence counts differ")
    for index, (sentence, row) in enumerate(zip(sentences, pos_rows)):
        if sentence != row["text"]:
            raise ValueError(f"POS gold text differs at sentence {index}")

    models: dict[str, Any] = {}

    jieba_words, jieba_seg_speed, jieba_load = run_jieba_segmentation(
        sentences, args.repeats
    )
    jieba_pos, jieba_pos_speed = run_jieba_pos(sentences, args.repeats, jieba_load)
    models["jieba"] = {
        "version": getattr(__import__("jieba"), "__version__", "unknown"),
        "segmentation": {
            "metrics": segmentation_metrics(sentences, official_gold, jieba_words),
            "speed": jieba_seg_speed,
        },
        "pos": {
            "metrics": pos_metrics(pos_rows, jieba_pos, "jieba", ckip_mapping),
            "speed": jieba_pos_speed,
        },
    }

    for name, spec in lingxi_specs.items():
        words, segmentation_speed = run_lingxi(
            spec["cli"], spec["assets"], sentences, "segmentation", args.repeats
        )
        tagged, pos_speed = run_lingxi(
            spec["cli"], spec["assets"], sentences, "pos", args.repeats
        )
        add_token_throughput(pos_speed, tagged)
        models[name] = {
            "version": spec["version"],
            "cli": str(spec["cli"].resolve()),
            "assets": str(spec["assets"].resolve()),
            "asset_bytes": asset_bytes(spec["assets"]),
            "segmentation": {
                "metrics": segmentation_metrics(sentences, official_gold, words),
                "speed": segmentation_speed,
            },
            "pos": {
                "metrics": pos_metrics(pos_rows, tagged, name, ckip_mapping),
                "speed": pos_speed,
            },
        }

    report = {
        "schema_version": 1,
        "dataset": {
            "segmentation_gold": "Trident chinese testdata BMES labels",
            "pos_gold": "saved CKIPTagger WS+POS silver annotations",
            "sentences": len(sentences),
            "characters": sum(map(len, sentences)),
            "official_segmentation_tokens": sum(map(len, official_gold)),
            "ckip_pos_tokens": sum(len(row["tokens"]) for row in pos_rows),
            "dataset_load_seconds_excluded": dataset_load_seconds,
            "fixed_prefix": True,
        },
        "protocol": {
            "repeats": args.repeats,
            "statistic": "median after warm-up",
            "segmentation_speed": "segmentation only",
            "pos_speed": "segmentation plus POS end-to-end",
            "lingxi_timing": "release CLI reported processing time including output serialization",
            "jieba_timing": "one in-process newline-delimited batch after explicit initialization",
            "pos_accuracy": "common mapped tag accuracy conditional on exactly aligned token spans",
            "pos_joint": "span plus common mapped POS micro F1",
            "lexical_metrics_exclude_punctuation": True,
        },
        "models": models,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output_markdown.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[report] {args.output_json}")
    print(f"[report] {args.output_markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
