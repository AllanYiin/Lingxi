from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

import torch
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader

from .data import (
    CharVocabulary,
    JsonlPunctuationDataset,
    TranscriptAugmentedDataset,
    build_vocabulary,
    collate_batch,
    extract_labeled_text,
    iter_records,
)
from .decoding import constrained_decode, greedy_unclosed, render_prediction
from .labels import PUNCTUATION_NAMES, QUOTE_NAMES
from .metrics import EvaluationAccumulator
from .model import ModelConfig, TinyPunctuationModel
from .trident_augmentation import TridentTextNoiseMixer
from .trident_data import prepare_trident

CHECKPOINT_FORMAT_VERSION = 1


def _optional_split(value: str) -> str | None:
    return None if value.lower() in {"none", "all", "*"} else value


def _dilations(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("dilations must be comma-separated integers") from error
    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("dilations must contain positive integers")
    return result


def _device(value: str) -> torch.device:
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA was requested but torch.cuda.is_available() is false")
    return device


def _move_batch(batch: Mapping[str, Any], device: torch.device) -> dict[str, Any]:
    return {
        key: value.to(device, non_blocking=True) if isinstance(value, torch.Tensor) else value
        for key, value in batch.items()
    }


def _loader(
    dataset: JsonlPunctuationDataset,
    *,
    batch_size: int,
    workers: int,
    device: torch.device,
) -> DataLoader[dict[str, Any]]:
    return DataLoader(
        dataset,
        batch_size=batch_size,
        num_workers=workers,
        collate_fn=collate_batch,
        pin_memory=device.type == "cuda",
    )


def _balanced_weights(counts: Counter[int], classes: int, cap: float) -> torch.Tensor:
    total = sum(counts.values())
    weights = []
    for class_index in range(classes):
        count = counts[class_index]
        if not count or not total:
            weights.append(1.0)
        else:
            weights.append(min(cap, math.sqrt(total / (classes * count))))
    mean = sum(weights) / len(weights)
    return torch.tensor([weight / mean for weight in weights], dtype=torch.float32)


def estimate_class_weights(
    paths: Sequence[Path],
    *,
    split: str | None,
    require_accepted: bool,
    max_chars: int,
    max_records: int | None,
    cap: float,
) -> tuple[torch.Tensor, torch.Tensor, dict[str, Any]]:
    punctuation: Counter[int] = Counter()
    quotes: Counter[int] = Counter()
    accepted = rejected = 0
    for record in iter_records(paths, split, require_accepted):
        if max_records is not None and accepted >= max_records:
            break
        try:
            example = extract_labeled_text(record)
        except ValueError:
            rejected += 1
            continue
        if len(example.units) > max_chars:
            rejected += 1
            continue
        punctuation.update(example.punctuation)
        quotes.update(example.quotes)
        accepted += 1
    return (
        _balanced_weights(punctuation, len(PUNCTUATION_NAMES), cap),
        _balanced_weights(quotes, len(QUOTE_NAMES), cap),
        {
            "accepted_records": accepted,
            "rejected_records": rejected,
            "punctuation_counts": {
                PUNCTUATION_NAMES[index]: punctuation[index]
                for index in range(len(PUNCTUATION_NAMES))
            },
            "quote_counts": {
                QUOTE_NAMES[index]: quotes[index] for index in range(len(QUOTE_NAMES))
            },
        },
    )


def save_checkpoint(
    path: Path,
    model: TinyPunctuationModel,
    vocabulary: CharVocabulary,
    metadata: Mapping[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "model_config": model.config.to_dict(),
        "model_state": model.state_dict(),
        "vocabulary": vocabulary.to_dict(),
        "labels": {
            "punctuation": list(PUNCTUATION_NAMES),
            "quotes": list(QUOTE_NAMES),
        },
        "metadata": dict(metadata),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(payload, temporary)
    temporary.replace(path)


def load_checkpoint(
    path: Path, device: torch.device
) -> tuple[TinyPunctuationModel, CharVocabulary, dict[str, Any]]:
    try:
        payload = torch.load(path, map_location=device, weights_only=False)
    except TypeError:
        payload = torch.load(path, map_location=device)
    if payload.get("format_version") != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("unsupported punctuation checkpoint format")
    if payload.get("labels") != {
        "punctuation": list(PUNCTUATION_NAMES),
        "quotes": list(QUOTE_NAMES),
    }:
        raise ValueError("checkpoint label schema does not match this runtime")
    vocabulary = CharVocabulary.from_dict(payload["vocabulary"])
    config = ModelConfig.from_dict(payload["model_config"])
    model = TinyPunctuationModel(config).to(device)
    model.load_state_dict(payload["model_state"])
    return model, vocabulary, dict(payload.get("metadata") or {})


@torch.inference_mode()
def evaluate_loader(
    model: TinyPunctuationModel,
    loader: DataLoader[dict[str, Any]],
    device: torch.device,
    *,
    style: str,
    predictions_handle: TextIO | None = None,
    max_batches: int | None = None,
) -> dict[str, Any]:
    model.eval()
    metrics = EvaluationAccumulator()
    for batch_index, cpu_batch in enumerate(loader):
        if max_batches is not None and batch_index >= max_batches:
            break
        batch = _move_batch(cpu_batch, device)
        outputs = model(batch)
        compatibility = model.pair_compatibility
        for row, length_value in enumerate(cpu_batch["lengths"].tolist()):
            length = int(length_value)
            decoded = constrained_decode(
                outputs["punctuation_logits"][row, :length],
                outputs["quote_logits"][row, :length],
                compatibility,
                cpu_batch["punctuation_allowed"][row, :length],
            )
            punctuation_targets = cpu_batch["punctuation"][row, :length].tolist()
            quote_targets = cpu_batch["quotes"][row, :length].tolist()
            was_unclosed = greedy_unclosed(outputs["quote_logits"][row], length)
            metrics.update(
                punctuation_targets,
                quote_targets,
                decoded.punctuation,
                decoded.quotes,
                greedy_was_unclosed=was_unclosed,
            )
            if predictions_handle is not None:
                units = cpu_batch["units"][row]
                prediction = render_prediction(
                    units,
                    decoded.punctuation,
                    decoded.quotes,
                    style=style,
                )
                predictions_handle.write(
                    json.dumps(
                        {
                            "source": cpu_batch["texts"][row],
                            "input": "".join(units),
                            "prediction": prediction,
                            "punctuation": [
                                PUNCTUATION_NAMES[label] for label in decoded.punctuation
                            ],
                            "quotes": [QUOTE_NAMES[label] for label in decoded.quotes],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    report = metrics.report()
    if not report["sequences"]:
        raise ValueError("evaluation dataset produced no valid examples")
    return report


def _train(args: argparse.Namespace) -> int:
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = _device(args.device)
    train_paths = tuple(args.train)
    dev_paths = tuple(args.dev)

    resume_metadata: dict[str, Any] = {}
    if args.resume is not None:
        model, vocabulary, resume_metadata = load_checkpoint(args.resume, device)
        config = model.config
        vocabulary_report: dict[str, Any] = {
            "resumed_from": str(args.resume),
            "vocabulary_size": len(vocabulary.id_to_char),
            "architecture_from_checkpoint": True,
        }
        start_epoch = int(resume_metadata.get("epoch", 0)) + 1
    else:
        vocabulary, vocabulary_report = build_vocabulary(
            train_paths,
            split=args.train_split,
            require_accepted=args.require_accepted,
            max_size=args.vocab_size,
            max_records=args.vocab_max_records,
        )
        config = ModelConfig(
            vocab_size=len(vocabulary.id_to_char),
            hash_buckets=args.hash_buckets,
            model_width=args.model_width,
            conv_dilations=args.conv_dilations,
            gru_hidden=args.gru_hidden,
            dropout=args.dropout,
        )
        model = TinyPunctuationModel(config).to(device)
        start_epoch = 1

    punctuation_weights, quote_weights, weight_report = estimate_class_weights(
        train_paths,
        split=args.train_split,
        require_accepted=args.require_accepted,
        max_chars=args.max_chars,
        max_records=args.weight_max_records,
        cap=args.class_weight_cap,
    )
    punctuation_weights = punctuation_weights.to(device)
    quote_weights = quote_weights.to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay
    )

    text_noise = None
    if args.trident_text_noise:
        text_noise = TridentTextNoiseMixer(
            weights={
                "clean": args.noise_clean_weight,
                "homophonic": args.noise_homophonic_weight,
                "chinese": args.noise_chinese_weight,
                "bopomofo": args.noise_bopomofo_weight,
                "homomorphic": args.noise_homomorphic_weight,
            },
            bopomofo_convert_ratio=args.bopomofo_convert_ratio,
            homophonic_convert_ratio=args.homophonic_convert_ratio,
            homomorphic_convert_ratio=args.homomorphic_convert_ratio,
        )

    base_train_dataset = JsonlPunctuationDataset(
        train_paths,
        vocabulary,
        split=args.train_split,
        require_accepted=args.require_accepted,
        hash_buckets=config.hash_buckets,
        max_chars=args.max_chars,
        shuffle_buffer=args.shuffle_buffer,
        seed=args.seed,
    )
    if args.transcript_augment_probability > 0.0:
        train_dataset: JsonlPunctuationDataset | TranscriptAugmentedDataset = (
            TranscriptAugmentedDataset(
                base_train_dataset,
                probability=args.transcript_augment_probability,
                min_records=args.transcript_min_records,
                max_records=args.transcript_max_records,
                whitespace_compaction_probability=args.whitespace_compaction_probability,
                text_noise=text_noise,
                seed=args.seed,
            )
        )
    else:
        train_dataset = base_train_dataset
    dev_dataset = JsonlPunctuationDataset(
        dev_paths,
        vocabulary,
        split=args.dev_split,
        require_accepted=args.require_accepted,
        hash_buckets=config.hash_buckets,
        max_chars=args.max_chars,
    )
    train_loader = _loader(
        train_dataset,
        batch_size=args.batch_size,
        workers=args.workers,
        device=device,
    )
    dev_loader = _loader(
        dev_dataset,
        batch_size=args.eval_batch_size,
        workers=args.workers,
        device=device,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    best_event_f1 = -1.0
    best_macro_f1 = -1.0
    baseline_report: dict[str, Any] | None = None
    if args.resume is not None:
        baseline_report = evaluate_loader(
            model,
            dev_loader,
            device,
            style=args.style,
            max_batches=args.eval_batches,
        )
        best_event_f1 = float(baseline_report["event_detection"]["f1"])
        best_macro_f1 = float(
            baseline_report["punctuation"]["macro_f1_present"]
        )
        baseline_metadata = {
            "epoch": start_epoch - 1,
            "best_event_f1": best_event_f1,
            "best_punctuation_macro_f1": best_macro_f1,
            "parameter_count": model.parameter_count(),
            "selection_metric": "dev.event_detection.f1",
            "loss_schema": "event-focused-v2",
            "resumed_from": str(args.resume),
            "optimizer_resumed": False,
        }
        save_checkpoint(
            args.output_dir / "best.pt", model, vocabulary, baseline_metadata
        )
        print(
            json.dumps(
                {
                    "resume_baseline_epoch": start_epoch - 1,
                    "dev_event_detection": baseline_report["event_detection"],
                    "dev_joint_non_none_accuracy": baseline_report[
                        "joint_non_none_accuracy"
                    ],
                    "dev_punctuation_macro_f1": best_macro_f1,
                },
                ensure_ascii=False,
            )
        )

    history: list[dict[str, Any]] = []
    for epoch in range(start_epoch, start_epoch + args.epochs):
        train_dataset.set_epoch(epoch)
        model.train()
        totals = Counter()
        steps = 0
        for step, cpu_batch in enumerate(train_loader, 1):
            if args.steps_per_epoch is not None and step > args.steps_per_epoch:
                break
            batch = _move_batch(cpu_batch, device)
            optimizer.zero_grad(set_to_none=True)
            outputs = model(batch)
            losses = model.loss(
                outputs,
                batch["punctuation"],
                batch["quotes"],
                punctuation_weights=punctuation_weights,
                quote_weights=quote_weights,
                quote_loss_weight=args.quote_loss_weight,
                pair_loss_weight=args.pair_loss_weight,
                event_loss_weight=args.event_loss_weight,
                positive_pair_loss_weight=args.positive_pair_loss_weight,
                event_pos_weight=args.event_pos_weight,
            )
            losses["loss"].backward()
            clip_grad_norm_(model.parameters(), args.gradient_clip)
            optimizer.step()
            for key, value in losses.items():
                totals[key] += float(value.item())
            steps += 1
        if not steps:
            raise ValueError("training dataset produced no valid examples")

        dev_report = evaluate_loader(
            model,
            dev_loader,
            device,
            style=args.style,
            max_batches=args.eval_batches,
        )
        epoch_report = {
            "epoch": epoch,
            "batches": steps,
            "train": {key: value / steps for key, value in totals.items()},
            "augmentation": dict(
                getattr(train_dataset, "augmentation_counts", {})
            ),
            "dev": dev_report,
        }
        history.append(epoch_report)
        print(json.dumps(epoch_report, ensure_ascii=False), flush=True)

        event_f1 = float(dev_report["event_detection"]["f1"])
        macro_f1 = float(dev_report["punctuation"]["macro_f1_present"])
        best_macro_f1 = max(best_macro_f1, macro_f1)
        metadata = {
            "epoch": epoch,
            "best_event_f1": max(best_event_f1, event_f1),
            "best_punctuation_macro_f1": best_macro_f1,
            "parameter_count": model.parameter_count(),
            "selection_metric": "dev.event_detection.f1",
            "loss_schema": "event-focused-v2",
            "resumed_from": str(args.resume) if args.resume is not None else None,
            "optimizer_resumed": False,
        }
        save_checkpoint(args.output_dir / "last.pt", model, vocabulary, metadata)
        if event_f1 > best_event_f1:
            best_event_f1 = event_f1
            save_checkpoint(args.output_dir / "best.pt", model, vocabulary, metadata)

    report = {
        "model_config": config.to_dict(),
        "parameter_count": model.parameter_count(),
        "estimated_fp32_bytes": model.parameter_count() * 4,
        "vocabulary": vocabulary_report,
        "class_statistics": weight_report,
        "selection_metric": "dev.event_detection.f1",
        "best_event_f1": best_event_f1,
        "best_punctuation_macro_f1": best_macro_f1,
        "resume": {
            "checkpoint": str(args.resume) if args.resume is not None else None,
            "checkpoint_metadata": resume_metadata,
            "optimizer_resumed": False,
            "baseline_dev": baseline_report,
        },
        "loss": {
            "schema": "event-focused-v2",
            "quote_loss_weight": args.quote_loss_weight,
            "pair_loss_weight": args.pair_loss_weight,
            "event_loss_weight": args.event_loss_weight,
            "positive_pair_loss_weight": args.positive_pair_loss_weight,
            "event_pos_weight": args.event_pos_weight,
        },
        "augmentation": {
            "transcript_probability": args.transcript_augment_probability,
            "min_records": args.transcript_min_records,
            "max_records": args.transcript_max_records,
            "whitespace_compaction_probability": args.whitespace_compaction_probability,
            "original_examples_are_preserved": True,
            "trident_text_noise": args.trident_text_noise,
            "noise_branch_weights": {
                "clean": args.noise_clean_weight,
                "homophonic": args.noise_homophonic_weight,
                "chinese": args.noise_chinese_weight,
                "bopomofo": args.noise_bopomofo_weight,
                "homomorphic": args.noise_homomorphic_weight,
            },
            "convert_ratios": {
                "bopomofo": args.bopomofo_convert_ratio,
                "homophonic": args.homophonic_convert_ratio,
                "homomorphic": args.homomorphic_convert_ratio,
            },
            "one_noise_branch_per_augmented_example": True,
        },
        "history": history,
    }
    (args.output_dir / "training-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: report[key] for key in report if key not in {"history", "resume"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    device = _device(args.device)
    model, vocabulary, metadata = load_checkpoint(args.checkpoint, device)
    dataset = JsonlPunctuationDataset(
        tuple(args.input),
        vocabulary,
        split=args.split,
        require_accepted=args.require_accepted,
        hash_buckets=model.config.hash_buckets,
        max_chars=args.max_chars,
    )
    loader = _loader(
        dataset,
        batch_size=args.batch_size,
        workers=args.workers,
        device=device,
    )
    handle = None
    try:
        if args.predictions is not None:
            args.predictions.parent.mkdir(parents=True, exist_ok=True)
            handle = args.predictions.open("w", encoding="utf-8", newline="\n")
        report = evaluate_loader(
            model,
            loader,
            device,
            style=args.style,
            predictions_handle=handle,
            max_batches=args.max_batches,
        )
    finally:
        if handle is not None:
            handle.close()
    report["checkpoint_metadata"] = metadata
    report["parameter_count"] = model.parameter_count()
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _inspect(args: argparse.Namespace) -> int:
    device = torch.device("cpu")
    model, vocabulary, metadata = load_checkpoint(args.checkpoint, device)
    print(
        json.dumps(
            {
                "model_config": model.config.to_dict(),
                "parameter_count": model.parameter_count(),
                "estimated_fp32_bytes": model.parameter_count() * 4,
                "vocabulary_size": len(vocabulary.id_to_char),
                "metadata": metadata,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _prepare_trident(args: argparse.Namespace) -> int:
    report = prepare_trident(
        args.output,
        max_records=args.max_records,
        overwrite=args.overwrite,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.punctuation_model",
        description="Train and evaluate LingXi's ultra-light punctuation restoration model.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare-trident",
        help="freeze Trident Chinese training text into punctuation labels",
    )
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--max-records", type=int)
    prepare.add_argument("--overwrite", action="store_true")

    train = commands.add_parser("train", help="train a new punctuation checkpoint")
    train.add_argument("--train", type=Path, action="append", required=True)
    train.add_argument("--dev", type=Path, action="append", required=True)
    train.add_argument("--output-dir", type=Path, required=True)
    train.add_argument(
        "--resume",
        type=Path,
        help="continue from checkpoint weights; optimizer starts fresh",
    )
    train.add_argument("--train-split", type=_optional_split, default="train")
    train.add_argument("--dev-split", type=_optional_split, default="dev")
    train.add_argument("--require-accepted", action="store_true")
    train.add_argument("--vocab-size", type=int, default=4096)
    train.add_argument("--vocab-max-records", type=int, default=200_000)
    train.add_argument("--weight-max-records", type=int, default=200_000)
    train.add_argument("--hash-buckets", type=int, default=1024)
    train.add_argument("--model-width", type=int, default=48)
    train.add_argument("--gru-hidden", type=int, default=64)
    train.add_argument("--conv-dilations", type=_dilations, default=(1, 2, 4, 8))
    train.add_argument("--dropout", type=float, default=0.1)
    train.add_argument("--max-chars", type=int, default=512)
    train.add_argument("--epochs", type=int, default=10)
    train.add_argument("--batch-size", type=int, default=64)
    train.add_argument("--eval-batch-size", type=int, default=128)
    train.add_argument("--steps-per-epoch", type=int)
    train.add_argument("--eval-batches", type=int)
    train.add_argument("--shuffle-buffer", type=int, default=4096)
    train.add_argument("--learning-rate", type=float, default=1e-3)
    train.add_argument("--weight-decay", type=float, default=1e-4)
    train.add_argument("--gradient-clip", type=float, default=1.0)
    train.add_argument("--class-weight-cap", type=float, default=8.0)
    train.add_argument("--quote-loss-weight", type=float, default=1.0)
    train.add_argument("--pair-loss-weight", type=float, default=0.1)
    train.add_argument("--event-loss-weight", type=float, default=1.0)
    train.add_argument("--positive-pair-loss-weight", type=float, default=1.0)
    train.add_argument("--event-pos-weight", type=float, default=4.0)
    train.add_argument(
        "--transcript-augment-probability", type=float, default=0.0
    )
    train.add_argument("--transcript-min-records", type=int, default=2)
    train.add_argument("--transcript-max-records", type=int, default=4)
    train.add_argument(
        "--whitespace-compaction-probability", type=float, default=0.5
    )
    train.add_argument("--trident-text-noise", action="store_true")
    train.add_argument("--noise-clean-weight", type=float, default=0.20)
    train.add_argument("--noise-homophonic-weight", type=float, default=0.40)
    train.add_argument("--noise-chinese-weight", type=float, default=0.20)
    train.add_argument("--noise-bopomofo-weight", type=float, default=0.12)
    train.add_argument("--noise-homomorphic-weight", type=float, default=0.08)
    train.add_argument("--bopomofo-convert-ratio", type=float, default=0.15)
    train.add_argument("--homophonic-convert-ratio", type=float, default=0.10)
    train.add_argument("--homomorphic-convert-ratio", type=float, default=0.04)
    train.add_argument("--workers", type=int, default=0)
    train.add_argument("--device", default="auto")
    train.add_argument("--style", choices=("zh-tw", "english"), default="zh-tw")
    train.add_argument("--seed", type=int, default=13)

    evaluate = commands.add_parser("evaluate", help="evaluate a frozen checkpoint")
    evaluate.add_argument("--checkpoint", type=Path, required=True)
    evaluate.add_argument("--input", type=Path, action="append", required=True)
    evaluate.add_argument("--split", type=_optional_split, default="test")
    evaluate.add_argument("--require-accepted", action="store_true")
    evaluate.add_argument("--predictions", type=Path)
    evaluate.add_argument("--report", type=Path)
    evaluate.add_argument("--style", choices=("zh-tw", "english"), default="zh-tw")
    evaluate.add_argument("--batch-size", type=int, default=128)
    evaluate.add_argument("--max-chars", type=int, default=512)
    evaluate.add_argument("--max-batches", type=int)
    evaluate.add_argument("--workers", type=int, default=0)
    evaluate.add_argument("--device", default="auto")

    inspect = commands.add_parser("inspect", help="print checkpoint metadata and size")
    inspect.add_argument("--checkpoint", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare-trident":
        return _prepare_trident(args)
    if args.command == "train":
        return _train(args)
    if args.command == "evaluate":
        return _evaluate(args)
    if args.command == "inspect":
        return _inspect(args)
    raise AssertionError(f"unhandled command: {args.command}")
