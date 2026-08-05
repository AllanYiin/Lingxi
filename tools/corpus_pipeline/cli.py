from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .annotate import preannotate_jsonl
from .evaluate import evaluate_jsonl, predict_jieba
from .models import build_models, validate_records
from .pipeline import prepare_from_manifest, read_jsonl

PACKAGE_DIR = Path(__file__).resolve().parent


def comma_set(value: str) -> set[str]:
    result = {item.strip() for item in value.split(",") if item.strip()}
    if not result:
        raise argparse.ArgumentTypeError("must contain at least one value")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.corpus_pipeline",
        description="Prepare reviewed Taiwanese Mandarin data and LingXi legacy JSON statistics.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="stream, filter, deduplicate and split pinned corpora"
    )
    prepare.add_argument("--manifest", type=Path, default=PACKAGE_DIR / "sources.toml")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--source", action="append", dest="sources")
    prepare.add_argument(
        "--count", type=int, help="override target count for every selected source"
    )
    prepare.add_argument("--max-chars", type=int, default=500)

    annotate = subparsers.add_parser(
        "preannotate", help="add CKIPTagger WS/POS suggestions"
    )
    annotate.add_argument("--input", type=Path, required=True)
    annotate.add_argument("--output", type=Path, required=True)
    annotate.add_argument("--model-dir", type=Path, required=True)
    annotate.add_argument(
        "--mapping", type=Path, default=PACKAGE_DIR / "ckip_to_lingxi.json"
    )
    annotate.add_argument("--splits", type=comma_set, default={"train", "dev"})
    annotate.add_argument("--batch-size", type=int, default=32)
    annotate.add_argument(
        "--cuda",
        action="store_true",
        help="enable CUDA instead of the safer CPU default",
    )

    validate = subparsers.add_parser(
        "validate", help="validate review state and lossless token reconstruction"
    )
    validate.add_argument("--input", type=Path, required=True)

    build = subparsers.add_parser(
        "build-model", help="build Dict and legacy BMES/POS HMM JSON files"
    )
    build.add_argument("--input", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--splits", type=comma_set, default={"train"})
    build.add_argument("--alpha", type=float, default=0.1)
    build.add_argument("--min-support", type=int, default=10)
    build.add_argument("--min-confidence", type=float, default=0.8)
    build.add_argument("--min-margin", type=float, default=0.2)

    jieba_parser = subparsers.add_parser(
        "predict-jieba", help="segment the frozen gold split with jieba"
    )
    jieba_parser.add_argument("--gold", type=Path, required=True)
    jieba_parser.add_argument("--output", type=Path, required=True)
    jieba_parser.add_argument("--split", default="test")

    evaluate = subparsers.add_parser(
        "evaluate", help="score generic JSONL predictions against reviewed gold"
    )
    evaluate.add_argument("--gold", type=Path, required=True)
    evaluate.add_argument("--predictions", type=Path, required=True)
    evaluate.add_argument("--split", default="test")
    evaluate.add_argument(
        "--train", type=Path, help="accepted train JSONL for OOV recall"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        if args.count is not None and args.count <= 0:
            raise SystemExit("--count must be positive")
        report = prepare_from_manifest(
            args.manifest,
            args.output,
            source_ids=set(args.sources) if args.sources else None,
            count_override=args.count,
            max_chars=args.max_chars,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "preannotate":
        counts = preannotate_jsonl(
            args.input,
            args.output,
            args.model_dir,
            args.mapping,
            include_splits=args.splits,
            batch_size=args.batch_size,
            disable_cuda=not args.cuda,
        )
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return 0

    if args.command == "validate":
        errors = validate_records(read_jsonl(args.input))
        if errors:
            for error in errors:
                print(error)
            print(f"validation failed: {len(errors)} error(s)")
            return 1
        print("validation passed")
        return 0

    if args.command == "build-model":
        if args.alpha <= 0:
            raise SystemExit("--alpha must be positive")
        report = build_models(
            args.input,
            args.output_dir,
            include_splits=args.splits,
            alpha=args.alpha,
            min_support=args.min_support,
            min_confidence=args.min_confidence,
            min_margin=args.min_margin,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "predict-jieba":
        counts = predict_jieba(args.gold, args.output, split=args.split)
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return 0

    if args.command == "evaluate":
        report = evaluate_jsonl(
            args.gold,
            args.predictions,
            split=args.split,
            train_path=args.train,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
