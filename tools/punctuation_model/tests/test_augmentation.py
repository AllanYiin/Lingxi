from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.punctuation_model.data import (
    CharVocabulary,
    JsonlPunctuationDataset,
    TranscriptAugmentedDataset,
)


class TranscriptAugmentationTests(unittest.TestCase):
    def test_preserves_originals_and_adds_interior_sentence_boundaries(self) -> None:
        rows = [
            {"split": "train", "text": "第一句。"},
            {"split": "train", "text": "第二句！"},
            {"split": "train", "text": "第三句？"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.jsonl"
            data.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            vocabulary = CharVocabulary(
                ("<PAD>", "<UNK>", "<BOS>", "第", "一", "二", "三", "句")
            )
            base = JsonlPunctuationDataset(
                (data,),
                vocabulary,
                split="train",
                require_accepted=False,
                hash_buckets=8,
                max_chars=64,
            )
            augmented = TranscriptAugmentedDataset(
                base,
                probability=1.0,
                min_records=2,
                max_records=2,
                whitespace_compaction_probability=0.0,
            )
            examples = list(augmented)

        self.assertEqual(len(examples), 5)
        joined = next(item for item in examples if item["text"] == "第一句。第二句！")
        self.assertGreater(sum(label != 0 for label in joined["punctuation"][:-1]), 0)


if __name__ == "__main__":
    unittest.main()
