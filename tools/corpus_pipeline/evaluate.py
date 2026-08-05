from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import validate_records
from .pipeline import is_han, read_jsonl, write_jsonl


def token_text(token: Any) -> str:
    if isinstance(token, str):
        return token
    if isinstance(token, Mapping) and isinstance(token.get("text"), str):
        return token["text"]
    raise ValueError(f"invalid token: {token!r}")


def token_spans(tokens: Sequence[Any]) -> list[tuple[int, int]]:
    spans = []
    cursor = 0
    for token in tokens:
        text = token_text(token)
        if not text:
            raise ValueError("empty token")
        end = cursor + len(text)
        spans.append((cursor, end))
        cursor = end
    return spans


def boundary_set(tokens: Sequence[Any]) -> set[int]:
    spans = token_spans(tokens)
    return {end for _, end in spans[:-1]}


def divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def accepted_gold(
    records: Iterable[Mapping[str, Any]], split: str
) -> list[Mapping[str, Any]]:
    selected = [
        record
        for record in records
        if record.get("split") == split
        and (record.get("review") or {}).get("status") == "accepted"
    ]
    errors = validate_records(selected)
    if errors:
        raise ValueError("invalid gold records:\n" + "\n".join(errors[:20]))
    if not selected:
        raise ValueError(f"no accepted gold records in split {split!r}")
    return selected


def training_vocabulary(records: Iterable[Mapping[str, Any]]) -> set[str]:
    vocabulary: set[str] = set()
    for record in records:
        if record.get("split") != "train":
            continue
        review = record.get("review") or {}
        if review.get("status") != "accepted":
            continue
        vocabulary.update(token["text"] for token in review["tokens"])
    return vocabulary


def evaluate_predictions(
    gold_records: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    vocabulary: set[str] | None = None,
) -> dict[str, Any]:
    prediction_by_id = {str(row["id"]): row for row in predictions}
    gold_boundaries = predicted_boundaries = matched_boundaries = 0
    exact_sentences = 0
    pos_correct = pos_total = 0
    oov_total = oov_matched = 0
    missing: list[str] = []
    for gold in gold_records:
        record_id = str(gold["id"])
        prediction = prediction_by_id.get(record_id)
        if prediction is None:
            missing.append(record_id)
            continue
        gold_tokens = gold["review"]["tokens"]
        predicted_tokens = prediction.get("tokens")
        if not isinstance(predicted_tokens, list):
            raise ValueError(f"{record_id}: prediction tokens must be a list")
        if "".join(token_text(token) for token in predicted_tokens) != gold["text"]:
            raise ValueError(
                f"{record_id}: prediction tokens do not reconstruct source text"
            )
        gold_boundary = boundary_set(gold_tokens)
        predicted_boundary = boundary_set(predicted_tokens)
        gold_boundaries += len(gold_boundary)
        predicted_boundaries += len(predicted_boundary)
        matched_boundaries += len(gold_boundary & predicted_boundary)
        gold_spans = token_spans(gold_tokens)
        predicted_spans = set(token_spans(predicted_tokens))
        if gold_spans == token_spans(predicted_tokens):
            exact_sentences += 1
            for gold_token, predicted_token in zip(gold_tokens, predicted_tokens):
                if (
                    isinstance(predicted_token, Mapping)
                    and gold_token.get("pos") is not None
                ):
                    pos_total += 1
                    pos_correct += predicted_token.get("pos") == gold_token.get("pos")
        if vocabulary is not None:
            for gold_token, span in zip(gold_tokens, gold_spans):
                if gold_token["text"] not in vocabulary and all(
                    is_han(char) for char in gold_token["text"]
                ):
                    oov_total += 1
                    oov_matched += span in predicted_spans
    if missing:
        raise ValueError(
            f"missing predictions for {len(missing)} records: {', '.join(missing[:10])}"
        )
    precision = divide(matched_boundaries, predicted_boundaries)
    recall = divide(matched_boundaries, gold_boundaries)
    report: dict[str, Any] = {
        "sentences": len(gold_records),
        "segmentation": {
            "boundary_precision": precision,
            "boundary_recall": recall,
            "boundary_f1": f1(precision, recall),
            "sentence_exact_match": divide(exact_sentences, len(gold_records)),
            "matched_boundaries": matched_boundaries,
            "gold_boundaries": gold_boundaries,
            "predicted_boundaries": predicted_boundaries,
        },
        "pos": {
            "accuracy_on_identical_segmentation": divide(pos_correct, pos_total),
            "correct": pos_correct,
            "total": pos_total,
        },
    }
    if vocabulary is not None:
        report["oov"] = {
            "token_recall": divide(oov_matched, oov_total),
            "matched": oov_matched,
            "total": oov_total,
        }
    return report


def evaluate_jsonl(
    gold_path: Path,
    prediction_path: Path,
    split: str = "test",
    train_path: Path | None = None,
) -> dict[str, Any]:
    gold = accepted_gold(list(read_jsonl(gold_path)), split)
    predictions = list(read_jsonl(prediction_path))
    vocabulary = training_vocabulary(read_jsonl(train_path)) if train_path else None
    return evaluate_predictions(gold, predictions, vocabulary)


def predict_jieba(
    gold_path: Path, output_path: Path, split: str = "test"
) -> dict[str, int]:
    try:
        import jieba
    except ImportError as error:
        raise RuntimeError(
            "predict-jieba requires the optional 'jieba' package"
        ) from error
    gold = accepted_gold(list(read_jsonl(gold_path)), split)
    predictions = []
    for record in gold:
        tokens = list(jieba.cut(record["text"], cut_all=False, HMM=True))
        if "".join(tokens) != record["text"]:
            raise ValueError(
                f"{record['id']}: jieba output does not reconstruct source text"
            )
        predictions.append({"id": record["id"], "tokens": tokens})
    write_jsonl(output_path, predictions)
    return {"predictions": len(predictions)}
