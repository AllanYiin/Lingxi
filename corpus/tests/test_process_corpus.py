from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from corpus.model_training import build_models, dictionary_reason
from corpus.retokenize import (
    RetokenizationEvidence,
    add_record_evidence,
    build_policy,
    canonical_quantity,
    canonicalize_record,
    is_quantity_surface,
)
from corpus.process_corpus import SourceRecord, annotate_batch


ROOT = Path(__file__).resolve().parents[2]

class ProcessCorpusTests(unittest.TestCase):
    def test_annotate_batch_keeps_ckip_pos_and_entities(self) -> None:
        def fake_ws(texts, **kwargs):
            self.assertFalse(kwargs["sentence_segmentation"])
            self.assertFalse(kwargs["character_normalization"])
            return [["長榮", "航空", "公司"]]

        def fake_pos(words, **kwargs):
            return [["Nb", "Na", "Nc"]]

        def fake_ner(words, tags):
            return [{(0, 6, "ORG", "長榮航空公司")}]

        rows = annotate_batch(
            [SourceRecord("fixture", 0, "長榮航空公司")],
            fake_ws,
            fake_pos,
            fake_ner,
        )
        self.assertEqual(rows[0]["tokens"][0]["ckip_pos"], "Nb")
        self.assertNotIn("pos", rows[0]["tokens"][0])
        self.assertEqual(rows[0]["entities"][0]["label"], "ORG")

    def test_dictionary_threshold_withholds_ambiguous_pos(self) -> None:
        accepted, reason = dictionary_reason(
            support=10,
            confidence=0.6,
            margin=0.2,
            entity_support=0,
            entity_confidence=0.0,
            min_support=5,
            min_pos_confidence=0.8,
            min_pos_margin=0.2,
            min_entity_support=2,
            min_entity_confidence=0.8,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "ambiguous-pos")

    def test_entity_evidence_cannot_bypass_minimum_word_support(self) -> None:
        accepted, reason = dictionary_reason(
            support=2,
            confidence=1.0,
            margin=1.0,
            entity_support=2,
            entity_confidence=1.0,
            min_support=10,
            min_pos_confidence=0.8,
            min_pos_margin=0.2,
            min_entity_support=2,
            min_entity_confidence=0.8,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "insufficient-support")

    def test_build_models_emits_order2_and_pos_files(self) -> None:
        records = [
            {
                "schema_version": 1,
                "source": "fixture",
                "source_index": 0,
                "text": "我愛臺灣",
                "tokens": [
                    {"text": "我", "ckip_pos": "Nh"},
                    {"text": "愛", "ckip_pos": "VC"},
                    {"text": "臺灣", "ckip_pos": "Nb"},
                ],
                "entities": [{"start": 2, "end": 4, "label": "GPE", "text": "臺灣"}],
            },
            {
                "schema_version": 1,
                "source": "fixture",
                "source_index": 1,
                "text": "臺灣真美",
                "tokens": [
                    {"text": "臺灣", "ckip_pos": "Nb"},
                    {"text": "真", "ckip_pos": "Dfa"},
                    {"text": "美", "ckip_pos": "VH"},
                ],
                "entities": [{"start": 0, "end": 2, "label": "GPE", "text": "臺灣"}],
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shard = root / "annotations" / "fixture" / "batch-00000000.jsonl"
            shard.parent.mkdir(parents=True)
            shard.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
                encoding="utf-8",
            )
            report = build_models(
                root,
                alpha=0.1,
                min_support=2,
                min_pos_confidence=0.8,
                min_pos_margin=0.2,
                min_entity_support=2,
                min_entity_confidence=0.8,
            )
            model = root / "model"
            expected = {
                "Dict.json",
                "Dict.evidence.jsonl",
                "Dict.review.jsonl",
                "NamedEntities.jsonl",
                "QuantityPatterns.jsonl",
                "startProbs.json",
                "transProbs.json",
                "transProbs2.json",
                "emmitProbs.json",
                "emmitProbs2.json",
                "bmesStateStats.json",
                "tagStartProbs.json",
                "tagTransProbs.json",
                "tagTransProbs2.json",
                "tagEmitProbs.json",
                "PosLexicon.json",
                "model-fingerprint.txt",
                "char_state_tab.json",
                "training-report.json",
            }
            self.assertTrue(expected.issubset({path.name for path in model.iterdir()}))
            dictionary = json.loads((model / "Dict.json").read_text(encoding="utf-8"))
            self.assertEqual(dictionary["臺灣"], ["Nb", 2])
            self.assertTrue(all(len(word) >= 2 for word in dictionary))
            emissions = json.loads((model / "emmitProbs.json").read_text(encoding="utf-8"))
            vocabulary = {char for row in emissions.values() for char in row if char != "<UNK>"}
            for state, row in emissions.items():
                unknown = row["<UNK>"]
                total = sum(math.exp(row.get(char, unknown)) for char in vocabulary) + math.exp(unknown)
                self.assertAlmostEqual(total, 1.0, places=9, msg=state)
            state_stats = json.loads(
                (model / "bmesStateStats.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state_stats["states"], ["B", "M", "E", "S"])
            self.assertAlmostEqual(sum(state_stats["state_marginals"].values()), 1.0)
            for entry in state_stats["characters"].values():
                self.assertEqual(
                    entry["support"], sum(entry["state_counts"])
                )
            self.assertEqual(report["pos_tagset"], "ckip")
            pos_start = json.loads((model / "tagStartProbs.json").read_text(encoding="utf-8"))
            self.assertIn("S-VC", pos_start)
            self.assertNotIn("S-v", pos_start)
            self.assertEqual(report["runtime_compatibility"]["bmes_order"], 2)
            self.assertEqual(report["runtime_compatibility"]["pos_runtime_order"], 2)

    def test_canonical_retokenization_merges_supported_named_entity(self) -> None:
        record = {
            "text": "長榮航空公司",
            "tokens": [
                {"text": "長榮", "ckip_pos": "Nb"},
                {"text": "航空", "ckip_pos": "Na"},
                {"text": "公司", "ckip_pos": "Nc"},
            ],
            "entities": [{"start": 0, "end": 6, "label": "ORG", "text": "長榮航空公司"}],
        }
        evidence = RetokenizationEvidence.empty()
        add_record_evidence(record, evidence)
        add_record_evidence(record, evidence)
        policy = build_policy(
            evidence,
            min_entity_support=2,
            min_entity_confidence=0.8,
            min_morpheme_support=2,
        )
        tokens, _, invalid = canonicalize_record(record, policy)
        self.assertFalse(invalid)
        self.assertEqual([token.text for token in tokens], ["長榮航空公司"])
        self.assertEqual(tokens[0].pos, "Nc")
        self.assertEqual(tokens[0].reason, "named-entity")

    def test_partial_ner_boundary_does_not_create_broken_name(self) -> None:
        record = {
            "text": "荊軻秦舞陽",
            "tokens": [
                {"text": "荊軻", "ckip_pos": "Nb"},
                {"text": "秦舞陽", "ckip_pos": "Nb"},
            ],
            "entities": [{"start": 0, "end": 3, "label": "PERSON", "text": "荊軻秦"}],
        }
        evidence = RetokenizationEvidence.empty()
        add_record_evidence(record, evidence)
        policy = build_policy(
            evidence,
            min_entity_support=1,
            min_entity_confidence=0.8,
            min_morpheme_support=1,
        )
        tokens, _, invalid = canonicalize_record(record, policy)
        self.assertEqual([token.text for token in tokens], ["荊軻", "秦舞陽"])
        self.assertEqual(invalid["partial-token-boundary"], 1)

    def test_quantity_uses_representative_without_replacing_surface(self) -> None:
        record = {
            "text": "三隻貓在2024年出生",
            "tokens": [
                {"text": "三", "ckip_pos": "Neu"},
                {"text": "隻", "ckip_pos": "Nf"},
                {"text": "貓", "ckip_pos": "Na"},
                {"text": "在", "ckip_pos": "P"},
                {"text": "2024", "ckip_pos": "Neu"},
                {"text": "年", "ckip_pos": "Nf"},
                {"text": "出生", "ckip_pos": "VA"},
            ],
            "entities": [
                {"start": 0, "end": 2, "label": "QUANTITY", "text": "三隻"},
                {"start": 4, "end": 9, "label": "DATE", "text": "2024年"},
            ],
        }
        policy = build_policy(
            RetokenizationEvidence.empty(),
            min_entity_support=2,
            min_entity_confidence=0.8,
            min_morpheme_support=2,
        )
        tokens, _, _ = canonicalize_record(record, policy)
        self.assertEqual(tokens[0].text, "三隻")
        self.assertEqual(tokens[0].canonical, "一隻")
        self.assertEqual(tokens[3].text, "2024年")
        self.assertEqual(tokens[3].canonical, "2000年")
        self.assertEqual(canonical_quantity("第十二屆", "ORDINAL"), "第一屆")
        self.assertEqual(canonical_quantity("百分之十五", "PERCENT"), "百分之一")
        self.assertEqual(
            canonical_quantity("台幣35億4700多萬元", "MONEY"), "台幣1元"
        )
        self.assertFalse(is_quantity_surface("星期四)上午十時"))

    def test_supported_noun_suffix_merges_forward(self) -> None:
        whole = {
            "text": "工程師",
            "tokens": [{"text": "工程師", "ckip_pos": "Na"}],
            "entities": [],
        }
        split = {
            "text": "工程師",
            "tokens": [
                {"text": "工程", "ckip_pos": "Na"},
                {"text": "師", "ckip_pos": "Na"},
            ],
            "entities": [],
        }
        evidence = RetokenizationEvidence.empty()
        add_record_evidence(whole, evidence)
        add_record_evidence(whole, evidence)
        add_record_evidence(split, evidence)
        policy = build_policy(
            evidence,
            min_entity_support=2,
            min_entity_confidence=0.8,
            min_morpheme_support=2,
        )
        tokens, _, _ = canonicalize_record(split, policy)
        self.assertEqual([token.text for token in tokens], ["工程師"])
        self.assertEqual(tokens[0].reason, "noun-suffix")

    def test_model_counts_follow_canonical_named_entity_boundary(self) -> None:
        records = [
            {
                "schema_version": 1,
                "source": "fixture",
                "source_index": index,
                "text": "長榮航空公司",
                "tokens": [
                    {"text": "長榮", "ckip_pos": "Nb"},
                    {"text": "航空", "ckip_pos": "Na"},
                    {"text": "公司", "ckip_pos": "Nc"},
                ],
                "entities": [
                    {"start": 0, "end": 6, "label": "ORG", "text": "長榮航空公司"}
                ],
            }
            for index in range(2)
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shard = root / "annotations" / "fixture" / "batch-00000000.jsonl"
            shard.parent.mkdir(parents=True)
            shard.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
                encoding="utf-8",
            )
            report = build_models(
                root,
                min_support=2,
                min_entity_support=2,
                min_entity_confidence=0.8,
                min_morpheme_support=2,
            )
            dictionary = json.loads((root / "model" / "Dict.json").read_text(encoding="utf-8"))
            self.assertEqual(dictionary["長榮航空公司"], ["Nc", 2])
            self.assertNotIn("長榮", dictionary)
            self.assertNotIn("航空", dictionary)
            self.assertNotIn("公司", dictionary)
            self.assertEqual(report["retokenization"]["token:named-entity"], 2)
            self.assertEqual(report["retokenization"]["tokens_removed"], 4)



if __name__ == "__main__":
    unittest.main()
