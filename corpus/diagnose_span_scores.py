"""Audit LingXi dictionary-substitution and BMES span scores on selected paths."""

from __future__ import annotations

import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / ".corpus-work" / "legacy-ckip-canonical-v3" / "model"
STATES = ("B", "M", "E", "S")


def load(name: str):
    return json.loads((MODEL / name).read_text(encoding="utf-8"))


START = load("startProbs.json")
TRANS1 = load("transProbs.json")
TRANS2 = load("transProbs2.json")
EMIT1 = load("emmitProbs.json")
EMIT2 = load("emmitProbs2.json")
DICTIONARY = load("Dict.json")
DICT_TOTAL = sum(float(value[1]) for value in DICTIONARY.values() if float(value[1]) > 0)


def shape(word: str) -> tuple[str, ...]:
    if len(word) == 1:
        return ("S",)
    if len(word) == 2:
        return ("B", "E")
    return ("B",) + ("M",) * (len(word) - 2) + ("E",)


def boundary(context: tuple[str, ...], state: str) -> float:
    if not context:
        return float(START[state])
    if len(context) == 1:
        return float(TRANS1[context[-1]][state])
    return float(TRANS2[context[-2]][context[-1]][state])


def emit(context: tuple[str, ...], state: str, char: str) -> float:
    if not context:
        return float(EMIT1[state].get(char, EMIT1[state]["<UNK>"]))
    row = EMIT2[context[-1]][state]
    return float(row.get(char, row["<UNK>"]))


def append(context: tuple[str, ...], states: tuple[str, ...]) -> tuple[str, ...]:
    return (context + states)[-2:]


def bmes_score(word: str, context: tuple[str, ...]) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    for char, state in zip(word, shape(word), strict=True):
        score += boundary(context, state) + emit(context, state, char)
        context = append(context, (state,))
    return score, context


def dictionary_score(word: str, context: tuple[str, ...]) -> tuple[float, tuple[str, ...]]:
    frequency = float(DICTIONARY[word][1])
    score = boundary(context, "B") + math.log(frequency / DICT_TOTAL)
    return score, append(context, shape(word))


def runtime_token_score(word: str, context: tuple[str, ...]) -> tuple[float, tuple[str, ...], str]:
    if len(word) >= 2 and word in DICTIONARY:
        score, result = dictionary_score(word, context)
        return score, result, f"dict({DICTIONARY[word][1]})"
    score, result = bmes_score(word, context)
    return score, result, "BMES"


def path_score(tokens: list[str], context: tuple[str, ...]) -> tuple[float, list[str]]:
    total = 0.0
    detail = []
    for word in tokens:
        score, context, source = runtime_token_score(word, context)
        total += score
        detail.append(f"{word}:{score:.6f}:{source}")
    return total, detail


CASES = [
    (("E", "S"), [["小威", "愈"], ["小威愈"]]),
    (("B", "E"), [["小森", "為"], ["小森為"]]),
    ((), [["對", "胃"], ["對胃"]]),
    ((), [["監督", "政府"], ["監督政府"]]),
    (("E", "S"), [["大會"], ["大", "會"]]),
    (("E", "S"), [["大事"], ["大", "事"]]),
    (("E", "S"), [["到來"], ["到", "來"]]),
]


FULL_PATHS = [
    [["反而", "是", "小威", "愈", "打", "愈", "弱"], ["反而", "是", "小威愈", "打", "愈", "弱"]],
    [["監督", "政府", "預算"], ["監督政府", "預算"]],
    [["對", "胃"], ["對胃"]],
    [["等待", "小森", "為", "他們", "奏出", "聖跡"], ["等待", "小森為", "他們", "奏出", "聖跡"]],
    [["丹紐", "的", "到來"], ["丹紐", "的", "到", "來"]],
]


def main() -> None:
    print(f"dictionary_total={DICT_TOTAL:.0f}")
    for context, paths in CASES:
        print(f"\ncontext={context or ('START',)}")
        for tokens in paths:
            score, detail = path_score(tokens, context)
            print(f"{score: .6f}  {'/'.join(tokens):<14}  {' | '.join(detail)}")
        for tokens in paths:
            if len(tokens) == 1 and len(tokens[0]) > 1 and tokens[0] in DICTIONARY:
                score, _ = bmes_score(tokens[0], context)
                print(f"{score: .6f}  {'/'.join(tokens):<14}  hypothetical BMES fallback")


    print("\nfull competing paths (START context)")
    for paths in FULL_PATHS:
        for tokens in paths:
            score, detail = path_score(tokens, ())
            print(f"{score: .6f}  {'/'.join(tokens)}")
            print(f"             {' | '.join(detail)}")
        print()


if __name__ == "__main__":
    main()
