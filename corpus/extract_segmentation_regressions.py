"""Extract exact 0.2.2 wins over 0.3.0 on Trident BMES gold."""

from __future__ import annotations

import json
from pathlib import Path

from compare_022_030_jieba import REPO_ROOT, lingxi_once, token_spans, words_from_bmes


def span_map(words: list[str]) -> dict[tuple[int, int], str]:
    return dict(zip(token_spans(words), words))


def main() -> int:
    from trident import load_examples_data

    ds = load_examples_data("chinese")
    data = list(ds.testdata.data.items)
    label = [t for t in ds.testdata.label.items[0]]
    label_bmes = list(ds.testdata.label.items[0].items)
    if len(data) != len(label) or len(data) != len(label_bmes):
        raise ValueError("data and label counts differ")
    gold = [
        words_from_bmes(sentence, answer) for sentence, answer in zip(data, label_bmes)
    ]

    cli_022 = REPO_ROOT / "dist" / "lingxi-0.2.2" / "cli-windows-x86_64" / "lingxi.exe"
    assets_022 = cli_022.parent / "assets"
    cli_030 = REPO_ROOT / "target" / "release" / "lingxi.exe"
    assets_030 = REPO_ROOT / "assets"

    prediction_022, _ = lingxi_once(cli_022, assets_022, data, "segmentation")
    prediction_030, _ = lingxi_once(cli_030, assets_030, data, "segmentation")

    regressions = []
    for index, (sentence, expected, old, new) in enumerate(
        zip(data, gold, prediction_022, prediction_030)
    ):
        if old != expected or new == expected:
            continue
        expected_map = span_map(expected)
        new_map = span_map(new)
        gold_only = [
            {"start": start, "end": end, "word": word}
            for (start, end), word in expected_map.items()
            if (start, end) not in new_map
        ]
        new_only = [
            {"start": start, "end": end, "word": word}
            for (start, end), word in new_map.items()
            if (start, end) not in expected_map
        ]
        regressions.append(
            {
                "index": index,
                "sentence": sentence,
                "characters": len(sentence),
                "gold": expected,
                "lingxi_0_2_2": old,
                "lingxi_0_3_0": new,
                "gold_only": gold_only,
                "lingxi_0_3_0_only": new_only,
                "span_error_count": len(gold_only) + len(new_only),
            }
        )

    regressions.sort(
        key=lambda row: (
            row["span_error_count"],
            row["characters"],
            row["index"],
        )
    )
    output = (
        REPO_ROOT
        / ".corpus-work"
        / "model-evaluation"
        / "trident-full-030-regressions-vs-022.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "criterion": (
                    "LingXi 0.2.2 token sequence exactly equals decoded Trident BMES "
                    "gold, while LingXi 0.3.0 does not"
                ),
                "dataset_sentences": len(data),
                "regression_sentences": len(regressions),
                "cases": regressions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "dataset_sentences": len(data),
                "regression_sentences": len(regressions),
                "first_cases": regressions[:30],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
