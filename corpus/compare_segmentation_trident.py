"""Full Trident segmentation comparison using ds.testdata.label as gold."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from compare_022_030_jieba import (
    REPO_ROOT,
    asset_bytes,
    run_jieba_segmentation,
    run_lingxi,
    segmentation_metrics,
    words_from_bmes,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPO_ROOT
        / ".corpus-work"
        / "model-evaluation"
        / "trident-full-segmentation-jieba-lingxi-022-030.json",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=REPO_ROOT
        / ".corpus-work"
        / "model-evaluation"
        / "trident-full-segmentation-jieba-lingxi-022-030.md",
    )
    return parser.parse_args()


def percentage(value: float) -> str:
    return f"{value * 100:.2f}%"


def markdown_report(report: dict[str, Any]) -> str:
    display = {
        "jieba": "jieba 0.42.1",
        "lingxi_0_2_2": "LingXi 0.2.2",
        "lingxi_0_3_0": "LingXi 0.3.0",
    }
    lines = [
        "# Trident 全量分詞比較",
        "",
        "測試資料嚴格依照：",
        "",
        "    data = list(ds.testdata.data.items)",
        "    label = [t for t in ds.testdata.label.items[0]]",
        "",
        f"句數：{report['dataset']['sentences']:,}；"
        f"字數：{report['dataset']['characters']:,}；"
        f"gold 詞數：{report['dataset']['gold_tokens']:,}。",
        "",
        "| 模型 | 載入時間 | 處理時間中位數 | 句／秒 | Word P | Word R | Word F1 | Boundary F1 | 句完全一致 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("jieba", "lingxi_0_2_2", "lingxi_0_3_0"):
        model = report["models"][key]
        metric = model["metrics"]
        speed = model["speed"]
        lines.append(
            f"| {display[key]} | {speed['load_seconds'] * 1000:.2f} ms | "
            f"{speed['processing_seconds'] * 1000:.2f} ms | "
            f"{speed['sentences_per_second']:,.1f} | "
            f"{percentage(metric['word_precision'])} | "
            f"{percentage(metric['word_recall'])} | "
            f"{percentage(metric['word_f1'])} | "
            f"{percentage(metric['boundary_f1'])} | "
            f"{percentage(metric['sentence_exact_match'])} |"
        )
    lines.extend(
        [
            "",
            "速度為暖機後三次中位數；資料集載入時間排除。"
            "LingXi 使用 release CLI 的純分詞 words 模式，jieba 使用精確分詞模式。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if args.repeats <= 0:
        raise SystemExit("--repeats must be positive")

    from trident import load_examples_data

    started = time.perf_counter()
    ds = load_examples_data("chinese")
    dataset_load_seconds = time.perf_counter() - started

    data = list(ds.testdata.data.items)
    label = [t for t in ds.testdata.label.items[0]]
    label_bmes = list(ds.testdata.label.items[0].items)
    if len(data) != len(label) or len(data) != len(label_bmes):
        raise ValueError(
            "data/encoded-label/BMES-label counts differ: "
            f"{len(data)} != {len(label)} != {len(label_bmes)}"
        )
    gold = [
        words_from_bmes(sentence, answer) for sentence, answer in zip(data, label_bmes)
    ]

    jieba_words, jieba_speed, _ = run_jieba_segmentation(data, args.repeats)
    models: dict[str, Any] = {
        "jieba": {
            "version": getattr(__import__("jieba"), "__version__", "unknown"),
            "metrics": segmentation_metrics(data, gold, jieba_words),
            "speed": jieba_speed,
        }
    }
    specs = {
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
    for name, spec in specs.items():
        words, speed = run_lingxi(
            spec["cli"], spec["assets"], data, "segmentation", args.repeats
        )
        models[name] = {
            "version": spec["version"],
            "cli": str(spec["cli"].resolve()),
            "assets": str(spec["assets"].resolve()),
            "asset_bytes": asset_bytes(spec["assets"]),
            "metrics": segmentation_metrics(data, gold, words),
            "speed": speed,
        }

    report = {
        "schema_version": 1,
        "dataset": {
            "source": "trident.load_examples_data('chinese')",
            "data_expression": "list(ds.testdata.data.items)",
            "label_expression": "[t for t in ds.testdata.label.items[0]]",
            "label_iteration_semantics": "encoded LabelDataset class ids",
            "label_bmes_decode": "list(ds.testdata.label.items[0].items)",
            "label_semantics": "decoded BMES segmentation gold from the same label dataset",
            "sentences": len(data),
            "characters": sum(map(len, data)),
            "gold_tokens": sum(map(len, gold)),
            "dataset_load_seconds_excluded": dataset_load_seconds,
        },
        "protocol": {
            "repeats": args.repeats,
            "statistic": "median after warm-up",
            "lingxi_mode": "release CLI words mode without POS",
            "jieba_mode": "jieba.cut(cut_all=False, HMM=True)",
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
