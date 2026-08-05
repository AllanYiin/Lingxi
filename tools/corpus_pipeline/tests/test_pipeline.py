from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.corpus_pipeline.annotate import annotate_records, load_tag_map, map_ckip_tag
from tools.corpus_pipeline.models import build_models, validate_records
from tools.corpus_pipeline.evaluate import evaluate_predictions
from tools.corpus_pipeline.pipeline import (
    SourceSpec,
    adapt_row,
    assign_split,
    dedupe_key,
    split_text,
    quality_rejection,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1]


class CorpusPipelineTests(unittest.TestCase):
    def test_twllm_adapter_keeps_only_real_human_prompts(self) -> None:
        spec = SourceSpec(
            id="real",
            adapter="twllm_real_prompts",
            dataset="x",
            config="x",
            split="train",
            revision="abc",
            license="Apache-2.0",
            license_url="https://example.test",
            target_count=1,
        )
        synthetic = {
            "dataset": "twllm-synthetic",
            "conversations": [{"from": "human", "value": "不要"}],
        }
        self.assertEqual(list(adapt_row(spec, synthetic, 0)), [])
        real = {
            "dataset": "v1log-geminipro",
            "conversations": [
                {"from": "human", "value": "保留"},
                {"from": "gpt", "value": "排除"},
            ],
        }
        rows = list(adapt_row(spec, real, 1))
        self.assertEqual([row[1] for row in rows], ["保留"])

    def test_patent_adapter_keeps_question_and_choices_in_one_document(self) -> None:
        spec = SourceSpec(
            id="patent",
            adapter="taiwan_patent_exam",
            dataset="x",
            config="default",
            split="train",
            revision="abc",
            license="Apache-2.0",
            license_url="https://example.test",
            target_count=5,
        )
        row = {
            "question": "題目",
            "A": "選項甲",
            "B": "選項乙",
            "C": "選項丙",
            "D": "選項丁",
            "answer": "D",
            "source": "fixture.xlsx",
        }
        rows = list(adapt_row(spec, row, 7))
        self.assertEqual(len(rows), 5)
        self.assertEqual({document_id for document_id, _, _ in rows}, {"7"})
        self.assertEqual(
            {metadata["field"] for _, _, metadata in rows},
            {"question", "A", "B", "C", "D"},
        )

    def test_split_thresholds_are_fixed_at_98_1_1(self) -> None:
        for bucket, expected in [(979, "train"), (980, "dev"), (989, "dev"), (990, "test")]:
            with patch("tools.corpus_pipeline.pipeline.stable_int", return_value=bucket):
                self.assertEqual(assign_split(7, "source", "document"), expected)
    def test_split_and_dedupe_are_deterministic(self) -> None:
        self.assertEqual(
            list(split_text("第一句。第二句！", max_chars=20)), ["第一句。", "第二句！"]
        )
        self.assertEqual(
            quality_rejection("請寄到 user@example.com 聯絡"), "suspected_pii"
        )
        self.assertIsNone(quality_rejection("這是一般的臺灣華語句子。"))
        self.assertEqual(dedupe_key("Ａ 臺灣\n"), dedupe_key("A臺灣"))
        self.assertEqual(assign_split(7, "s", "d"), assign_split(7, "s", "d"))

    def test_preannotation_preserves_whitespace_and_leaves_ambiguity_open(self) -> None:
        records = [
            {
                "split": "train",
                "text": "我 愛臺灣",
                "preannotation": None,
                "review": {"status": "pending", "tokens": None},
            },
            {
                "split": "test",
                "text": "盲測",
                "preannotation": None,
                "review": {"status": "pending", "tokens": None},
            },
        ]
        mapping = load_tag_map(PACKAGE_DIR / "ckip_to_lingxi.json")

        def fake_annotator(texts):
            self.assertEqual(texts, ["我 愛臺灣"])
            return [(["我", "愛", "臺灣"], ["Nh", "VC", "Nb"])]

        counts = annotate_records(records, fake_annotator, mapping, batch_size=4)
        self.assertEqual(counts, {"annotated": 1, "errors": 0, "skipped": 1})
        tokens = records[0]["preannotation"]["tokens"]
        self.assertEqual("".join(token["text"] for token in tokens), "我 愛臺灣")
        self.assertEqual(tokens[-1]["mapping"], "ambiguous")
        self.assertIsNone(records[0]["review"]["tokens"][-1]["pos"])
        self.assertIsNone(records[1]["preannotation"])
        self.assertEqual(map_ckip_tag("Na", mapping), ("n", "exact", ["n"]))

    def test_evaluation_uses_boundaries_and_optional_pos(self) -> None:
        gold_one = self.accepted_record(
            "g1",
            "臺灣好",
            [{"text": "臺灣", "pos": "ns"}, {"text": "好", "pos": "a"}],
        )
        gold_two = self.accepted_record(
            "g2",
            "我愛你",
            [
                {"text": "我", "pos": "r"},
                {"text": "愛", "pos": "v"},
                {"text": "你", "pos": "r"},
            ],
        )
        gold_one["split"] = gold_two["split"] = "test"
        predictions = [
            {
                "id": "g1",
                "tokens": [{"text": "臺灣", "pos": "ns"}, {"text": "好", "pos": "v"}],
            },
            {"id": "g2", "tokens": ["我愛", "你"]},
        ]
        report = evaluate_predictions(
            [gold_one, gold_two], predictions, vocabulary={"我", "愛", "好"}
        )
        self.assertAlmostEqual(report["segmentation"]["boundary_precision"], 1.0)
        self.assertAlmostEqual(report["segmentation"]["boundary_recall"], 2 / 3)
        self.assertAlmostEqual(report["segmentation"]["sentence_exact_match"], 0.5)
        self.assertAlmostEqual(report["pos"]["accuracy_on_identical_segmentation"], 0.5)
        self.assertAlmostEqual(report["oov"]["token_recall"], 1.0)

    def test_validation_rejects_lossy_review(self) -> None:
        record = {
            "schema_version": 1,
            "id": "bad",
            "split": "train",
            "text": "臺灣",
            "review": {"status": "accepted", "tokens": [{"text": "台灣", "pos": "ns"}]},
        }
        errors = validate_records([record])
        self.assertTrue(any("reconstruct" in error for error in errors))

    def test_build_models_emits_converter_compatible_files(self) -> None:
        records = [
            self.accepted_record(
                "one",
                "我愛臺灣。",
                [
                    {"text": "我", "pos": "r"},
                    {"text": "愛", "pos": "v"},
                    {"text": "臺灣", "pos": "ns"},
                    {"text": "。", "pos": None},
                ],
            ),
            self.accepted_record(
                "two",
                "臺灣真美",
                [
                    {"text": "臺灣", "pos": "ns"},
                    {"text": "真", "pos": "d"},
                    {"text": "美", "pos": "a"},
                ],
            ),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reviewed.jsonl"
            source.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
                encoding="utf-8",
            )
            output = root / "model"
            report = build_models(source, output, min_support=2)
            expected = {
                "Dict.json",
                "VariantWords.json",
                "startProbs.json",
                "transProbs.json",
                "transProbs2.json",
                "emmitProbs.json",
                "emmitProbs2.json",
                "r_emmitProbs.json",
                "bmesStateStats.json",
                "tagStartProbs.json",
                "tagTransProbs.json",
                "tagEmitProbs.json",
                "char_state_tab.json",
                "training-report.json",
            }
            self.assertTrue(expected.issubset({path.name for path in output.iterdir()}))
            dictionary = json.loads((output / "Dict.json").read_text(encoding="utf-8"))
            self.assertEqual(dictionary["臺灣"], ["ns", 2])
            self.assertEqual(report["accepted_sequences"], 2)
            state_stats = json.loads(
                (output / "bmesStateStats.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state_stats["states"], ["B", "M", "E", "S"])
            self.assertAlmostEqual(sum(state_stats["state_marginals"].values()), 1.0)
            for entry in state_stats["characters"].values():
                self.assertEqual(
                    entry["support"], sum(entry["state_counts"])
                )
            start = json.loads((output / "startProbs.json").read_text(encoding="utf-8"))
            self.assertLess(start["M"], -1e29)

    @staticmethod
    def accepted_record(record_id, text, tokens):
        return {
            "schema_version": 1,
            "id": record_id,
            "source": {"id": "fixture"},
            "document_id": record_id,
            "segment_index": 0,
            "split": "train",
            "text": text,
            "metadata": {},
            "preannotation": None,
            "review": {
                "status": "accepted",
                "tokens": tokens,
                "annotator": "test",
                "notes": "",
            },
        }


if __name__ == "__main__":
    unittest.main()
