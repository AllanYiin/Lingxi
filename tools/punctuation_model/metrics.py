from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .labels import PUNCTUATION_NAMES, QUOTE_NAMES


@dataclass
class ClassificationCounts:
    names: Sequence[str]
    true_positive: list[int] = field(init=False)
    false_positive: list[int] = field(init=False)
    false_negative: list[int] = field(init=False)

    def __post_init__(self) -> None:
        self.true_positive = [0] * len(self.names)
        self.false_positive = [0] * len(self.names)
        self.false_negative = [0] * len(self.names)

    def update(self, targets: Sequence[int], predictions: Sequence[int]) -> None:
        if len(targets) != len(predictions):
            raise ValueError("target and prediction lengths differ")
        for target, prediction in zip(targets, predictions):
            if target == prediction:
                self.true_positive[target] += 1
            else:
                self.false_negative[target] += 1
                self.false_positive[prediction] += 1

    def report(self, *, exclude_none_from_macro: bool) -> dict[str, object]:
        rows: dict[str, dict[str, float | int]] = {}
        f1_values: list[float] = []
        present_f1_values: list[float] = []
        start_index = 1 if exclude_none_from_macro else 0
        for index, name in enumerate(self.names):
            tp = self.true_positive[index]
            fp = self.false_positive[index]
            fn = self.false_negative[index]
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            rows[name] = {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": tp + fn,
            }
            if index >= start_index:
                f1_values.append(f1)
                if tp + fn:
                    present_f1_values.append(f1)
        return {
            "per_class": rows,
            "macro_f1": sum(f1_values) / len(f1_values) if f1_values else 0.0,
            "macro_f1_present": (
                sum(present_f1_values) / len(present_f1_values)
                if present_f1_values
                else 0.0
            ),
        }


@dataclass
class EvaluationAccumulator:
    punctuation: ClassificationCounts = field(
        default_factory=lambda: ClassificationCounts(PUNCTUATION_NAMES)
    )
    quotes: ClassificationCounts = field(
        default_factory=lambda: ClassificationCounts(QUOTE_NAMES)
    )
    all_boundary_total: int = 0
    all_boundary_correct: int = 0
    non_none_boundary_total: int = 0
    non_none_boundary_correct: int = 0
    empty_boundary_total: int = 0
    false_insertions: int = 0
    event_true_positive: int = 0
    event_false_positive: int = 0
    event_false_negative: int = 0
    quote_sequence_exact: int = 0
    sequences: int = 0
    greedy_unclosed: int = 0

    def update(
        self,
        punctuation_targets: Sequence[int],
        quote_targets: Sequence[int],
        punctuation_predictions: Sequence[int],
        quote_predictions: Sequence[int],
        *,
        greedy_was_unclosed: bool,
    ) -> None:
        self.punctuation.update(punctuation_targets, punctuation_predictions)
        self.quotes.update(quote_targets, quote_predictions)
        for target_punctuation, target_quote, predicted_punctuation, predicted_quote in zip(
            punctuation_targets,
            quote_targets,
            punctuation_predictions,
            quote_predictions,
        ):
            gold_event = target_punctuation != 0 or target_quote != 0
            predicted_event = predicted_punctuation != 0 or predicted_quote != 0
            exact = (
                target_punctuation == predicted_punctuation
                and target_quote == predicted_quote
            )
            self.all_boundary_total += 1
            self.all_boundary_correct += int(exact)
            if gold_event:
                self.non_none_boundary_total += 1
                self.non_none_boundary_correct += int(exact)
            else:
                self.empty_boundary_total += 1
                self.false_insertions += int(predicted_event)
            if gold_event and predicted_event:
                self.event_true_positive += 1
            elif predicted_event:
                self.event_false_positive += 1
            elif gold_event:
                self.event_false_negative += 1
        self.quote_sequence_exact += int(list(quote_targets) == list(quote_predictions))
        self.greedy_unclosed += int(greedy_was_unclosed)
        self.sequences += 1

    def report(self) -> dict[str, object]:
        event_precision = (
            self.event_true_positive
            / (self.event_true_positive + self.event_false_positive)
            if self.event_true_positive + self.event_false_positive
            else 0.0
        )
        event_recall = (
            self.event_true_positive
            / (self.event_true_positive + self.event_false_negative)
            if self.event_true_positive + self.event_false_negative
            else 0.0
        )
        event_f1 = (
            2 * event_precision * event_recall / (event_precision + event_recall)
            if event_precision + event_recall
            else 0.0
        )
        return {
            "punctuation": self.punctuation.report(exclude_none_from_macro=True),
            "quotes": self.quotes.report(exclude_none_from_macro=True),
            "joint_non_none_accuracy": (
                self.non_none_boundary_correct / self.non_none_boundary_total
                if self.non_none_boundary_total
                else 0.0
            ),
            "all_boundary_accuracy": (
                self.all_boundary_correct / self.all_boundary_total
                if self.all_boundary_total
                else 0.0
            ),
            "false_insertion_rate": (
                self.false_insertions / self.empty_boundary_total
                if self.empty_boundary_total
                else 0.0
            ),
            "event_detection": {
                "precision": event_precision,
                "recall": event_recall,
                "f1": event_f1,
            },
            "quote_sequence_exact_match": (
                self.quote_sequence_exact / self.sequences if self.sequences else 0.0
            ),
            "greedy_unclosed_rate": (
                self.greedy_unclosed / self.sequences if self.sequences else 0.0
            ),
            "constrained_unclosed_rate": 0.0,
            "sequences": self.sequences,
            "boundaries": self.all_boundary_total,
            "non_none_boundaries": self.non_none_boundary_total,
            "empty_boundaries": self.empty_boundary_total,
        }
