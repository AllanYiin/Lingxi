from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import torch

from .labels import PunctuationLabel, QuoteLabel, render_boundary


@dataclass(frozen=True)
class DecodedSequence:
    punctuation: tuple[int, ...]
    quotes: tuple[int, ...]
    score: float


def constrained_decode(
    punctuation_logits: torch.Tensor,
    quote_logits: torch.Tensor,
    pair_compatibility: torch.Tensor,
    punctuation_allowed: torch.Tensor | Sequence[bool],
) -> DecodedSequence:
    """Decode one sequence while enforcing a depth-1 balanced quote language."""
    if punctuation_logits.ndim != 2 or quote_logits.ndim != 2:
        raise ValueError("logits must have shape [time, classes]")
    if punctuation_logits.size(0) != quote_logits.size(0):
        raise ValueError("punctuation and quote logits must have the same length")
    if pair_compatibility.shape != (
        punctuation_logits.size(1),
        quote_logits.size(1),
    ):
        raise ValueError("pair_compatibility has incompatible shape")

    pair_scores = (
        punctuation_logits.unsqueeze(-1)
        + quote_logits.unsqueeze(-2)
        + pair_compatibility.unsqueeze(0)
    ).detach().float().cpu()
    allowed = torch.as_tensor(punctuation_allowed, dtype=torch.bool).cpu()
    if allowed.numel() != pair_scores.size(0):
        raise ValueError("punctuation_allowed has incompatible length")
    pair_scores[~allowed, 1:, :] = -torch.inf

    # OPEN changes 0→1, CLOSE changes 1→0, NONE preserves depth.
    transitions = {
        0: ((int(QuoteLabel.NONE), 0), (int(QuoteLabel.OPEN), 1)),
        1: ((int(QuoteLabel.NONE), 1), (int(QuoteLabel.CLOSE), 0)),
    }
    negative_infinity = float("-inf")
    scores = [0.0, negative_infinity]
    backpointers: list[list[tuple[int, int, int] | None]] = []

    for time_index in range(pair_scores.size(0)):
        next_scores = [negative_infinity, negative_infinity]
        step: list[tuple[int, int, int] | None] = [None, None]
        for previous_depth in (0, 1):
            if scores[previous_depth] == negative_infinity:
                continue
            for quote_label, next_depth in transitions[previous_depth]:
                best_punctuation_score, best_punctuation = torch.max(
                    pair_scores[time_index, :, quote_label], dim=0
                )
                candidate = scores[previous_depth] + float(best_punctuation_score.item())
                if candidate > next_scores[next_depth]:
                    next_scores[next_depth] = candidate
                    step[next_depth] = (
                        previous_depth,
                        int(best_punctuation.item()),
                        quote_label,
                    )
        scores = next_scores
        backpointers.append(step)

    if scores[0] == negative_infinity:
        raise ValueError("no balanced quote path exists")
    punctuation: list[int] = []
    quotes: list[int] = []
    depth = 0
    for step in reversed(backpointers):
        decision = step[depth]
        if decision is None:
            raise AssertionError("decoder backpointer is missing")
        previous_depth, punctuation_label, quote_label = decision
        punctuation.append(punctuation_label)
        quotes.append(quote_label)
        depth = previous_depth
    punctuation.reverse()
    quotes.reverse()
    return DecodedSequence(tuple(punctuation), tuple(quotes), scores[0])


def greedy_unclosed(quotes: torch.Tensor, length: int) -> bool:
    depth = 0
    for quote_label in quotes[:length].argmax(dim=-1).detach().cpu().tolist():
        if quote_label == int(QuoteLabel.OPEN):
            depth += 1
        elif quote_label == int(QuoteLabel.CLOSE):
            depth -= 1
        if depth not in (0, 1):
            return True
    return depth != 0


def render_prediction(
    units: Sequence[str],
    punctuation: Sequence[int],
    quotes: Sequence[int],
    *,
    style: str,
) -> str:
    if len(punctuation) != len(units) + 1 or len(quotes) != len(units) + 1:
        raise ValueError("boundary predictions must include BOS plus one item per unit")
    pieces = [render_boundary(punctuation[0], quotes[0], style)]
    for index, unit in enumerate(units, 1):
        pieces.append(unit)
        pieces.append(render_boundary(punctuation[index], quotes[index], style))
    return "".join(pieces)
