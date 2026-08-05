from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Sequence

from compare_022_030_jieba import (
    REPO_ROOT,
    run_lingxi,
    segmentation_metrics,
    token_spans,
    words_from_bmes,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare smoothed reverse-emission weights on Trident Chinese test data."
    )
    parser.add_argument(
        "--cli",
        type=Path,
        default=REPO_ROOT / "target" / "release" / "lingxi.exe",
    )
    parser.add_argument("--current-assets", type=Path, default=REPO_ROOT / "assets")
    parser.add_argument(
        "--experimental-assets",
        type=Path,
        default=REPO_ROOT
        / ".corpus-work"
        / "reverse-emission-experiment"
        / "assets",
    )
    parser.add_argument(
        "--weights",
        type=float,
        nargs="+",
        default=[0.0, 0.1, 0.25, 0.5, 0.75, 1.0],
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPO_ROOT
        / ".corpus-work"
        / "model-evaluation"
        / "reverse-emission-trident.json",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=REPO_ROOT
        / ".corpus-work"
        / "model-evaluation"
        / "reverse-emission-trident.md",
    )
    return parser.parse_args()


def oov_metrics(
    gold: Sequence[Sequence[str]],
    predicted: Sequence[Sequence[str]],
    training_vocabulary: set[str],
) -> dict[str, Any]:
    total = matched = 0
    for expected, actual in zip(gold, predicted):
        actual_spans = set(token_spans(actual))
        for word, span in zip(expected, token_spans(expected)):
            if word not in training_vocabulary:
                total += 1
                matched += span in actual_spans
    return {
        "gold_oov_tokens": total,
        "matched_oov_tokens": matched,
        "oov_recall": matched / total if total else 0.0,
    }


def comparison_counts(
    gold: Sequence[Sequence[str]],
    baseline: Sequence[Sequence[str]],
    candidate: Sequence[Sequence[str]],
) -> dict[str, int]:
    changed = improved = regressed = 0
    for expected, before, after in zip(gold, baseline, candidate):
        changed += list(before) != list(after)
        improved += list(before) != list(expected) and list(after) == list(expected)
        regressed += list(before) == list(expected) and list(after) != list(expected)
    return {
        "changed_sentences": changed,
        "exact_improvements": improved,
        "exact_regressions": regressed,
    }


def row(
    sentences: Sequence[str],
    gold: Sequence[Sequence[str]],
    predictions: Sequence[Sequence[str]],
    training_vocabulary: set[str],
    speed: dict[str, Any],
    baseline_metrics: dict[str, Any],
    baseline_predictions: Sequence[Sequence[str]],
) -> dict[str, Any]:
    metrics = segmentation_metrics(sentences, gold, predictions)
    return {
        "metrics": metrics,
        "oov": oov_metrics(gold, predictions, training_vocabulary),
        "speed": speed,
        "delta_from_current": {
            "word_f1": metrics["word_f1"] - baseline_metrics["word_f1"],
            "boundary_f1": metrics["boundary_f1"]
            - baseline_metrics["boundary_f1"],
            "sentence_exact_match": metrics["sentence_exact_match"]
            - baseline_metrics["sentence_exact_match"],
        },
        **comparison_counts(gold, baseline_predictions, predictions),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Reverse emission / Trident 效度比較",
        "",
        (
            f"資料集：{report['dataset']['sentences']:,} 句、"
            f"{report['dataset']['characters']:,} 字、"
            f"{report['dataset']['gold_tokens']:,} gold 詞；"
            f"訓練詞彙 {report['dataset']['training_vocabulary']:,}。"
        ),
        "",
        (
            "實驗資產 weight=0 與現行資產輸出："
            + (
                "完全一致。"
                if report["baseline_equivalence"]["predictions_equal"]
                else "不一致，結果不可直接歸因於 reverse emission。"
            )
        ),
        "",
        "| 模式 | Word F1 | Δ Word F1 | Boundary F1 | Exact | OOV recall | 改變句 | 改善/退步 | 句/秒 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    current = report["current"]
    lines.append(
        "| current | "
        f"{current['metrics']['word_f1']:.6f} | 0 | "
        f"{current['metrics']['boundary_f1']:.6f} | "
        f"{current['metrics']['sentence_exact_match']:.6f} | "
        f"{current['oov']['oov_recall']:.6f} | 0 | 0/0 | "
        f"{current['speed']['sentences_per_second']:.1f} |"
    )
    for result in report["weights"]:
        lines.append(
            f"| w={result['weight']:g} | "
            f"{result['metrics']['word_f1']:.6f} | "
            f"{result['delta_from_current']['word_f1']:+.6f} | "
            f"{result['metrics']['boundary_f1']:.6f} | "
            f"{result['metrics']['sentence_exact_match']:.6f} | "
            f"{result['oov']['oov_recall']:.6f} | "
            f"{result['changed_sentences']} | "
            f"{result['exact_improvements']}/{result['exact_regressions']} | "
            f"{result['speed']['sentences_per_second']:.1f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    if args.repeats <= 0:
        raise SystemExit("--repeats must be positive")
    if not args.weights or any(not 0.0 <= weight <= 1.0 for weight in args.weights):
        raise SystemExit("--weights must contain values between 0 and 1")
    weights = list(dict.fromkeys(args.weights))
    if 0.0 not in weights:
        weights.insert(0, 0.0)

    from trident import load_examples_data

    started = time.perf_counter()
    ds = load_examples_data("chinese")
    dataset_load_seconds = time.perf_counter() - started
    sentences = list(ds.testdata.data.items)
    labels = list(ds.testdata.label.items[0].items)
    gold = [
        words_from_bmes(sentence, answer)
        for sentence, answer in zip(sentences, labels)
    ]
    train_sentences = list(ds.traindata.data.items)
    train_labels = list(ds.traindata.label.items[0].items)
    training_vocabulary = {
        word
        for sentence, answer in zip(train_sentences, train_labels)
        for word in words_from_bmes(sentence, answer)
    }

    current_predictions, current_speed = run_lingxi(
        args.cli,
        args.current_assets,
        sentences,
        "segmentation",
        args.repeats,
        ["--reverse-weight", "0"],
    )
    current_metrics = segmentation_metrics(sentences, gold, current_predictions)
    current = row(
        sentences,
        gold,
        current_predictions,
        training_vocabulary,
        current_speed,
        current_metrics,
        current_predictions,
    )

    results = []
    experimental_predictions: dict[float, Sequence[Sequence[str]]] = {}
    for weight in weights:
        predictions, speed = run_lingxi(
            args.cli,
            args.experimental_assets,
            sentences,
            "segmentation",
            args.repeats,
            ["--reverse-weight", str(weight)],
        )
        experimental_predictions[weight] = predictions
        results.append(
            {
                "weight": weight,
                **row(
                    sentences,
                    gold,
                    predictions,
                    training_vocabulary,
                    speed,
                    current_metrics,
                    current_predictions,
                ),
            }
        )

    zero_predictions = experimental_predictions[0.0]
    report = {
        "schema_version": 1,
        "dataset": {
            "source": "trident.load_examples_data('chinese')",
            "sentences": len(sentences),
            "characters": sum(map(len, sentences)),
            "gold_tokens": sum(map(len, gold)),
            "training_vocabulary": len(training_vocabulary),
            "dataset_load_seconds_excluded": dataset_load_seconds,
        },
        "protocol": {
            "repeats": args.repeats,
            "statistic": "median after warm-up",
            "weights": weights,
            "reverse_prior": "Dirichlet state marginal, total concentration 1.0",
            "reverse_scope": "OOV token initial state only (S for len=1, B otherwise), excluding dictionary-anchored boundaries",
        },
        "paths": {
            "cli": str(args.cli.resolve()),
            "current_assets": str(args.current_assets.resolve()),
            "experimental_assets": str(args.experimental_assets.resolve()),
        },
        "baseline_equivalence": {
            "predictions_equal": zero_predictions == current_predictions,
            "changed_sentences": sum(
                before != after
                for before, after in zip(current_predictions, zero_predictions)
            ),
        },
        "current": current,
        "weights": results,
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
