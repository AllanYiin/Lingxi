from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.punctuation_model.cli import main


class CliSmokeTests(unittest.TestCase):
    def test_train_and_evaluate_commands(self) -> None:
        rows = [
            {"split": "train", "text": "今天下雨，記得帶傘。"},
            {"split": "train", "text": "他說：「你好。」"},
            {"split": "train", "text": "你明天會來嗎？"},
            {"split": "train", "text": "很好！我們出發。"},
            {"split": "dev", "text": "現在下雨，晚點再走。"},
            {"split": "dev", "text": "他問：「可以嗎？」"},
            {"split": "test", "text": "天氣很好，我們散步。"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.jsonl"
            data.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            output = root / "model"
            self.assertEqual(
                main(
                    [
                        "train",
                        "--train",
                        str(data),
                        "--dev",
                        str(data),
                        "--output-dir",
                        str(output),
                        "--epochs",
                        "1",
                        "--steps-per-epoch",
                        "1",
                        "--eval-batches",
                        "1",
                        "--batch-size",
                        "2",
                        "--eval-batch-size",
                        "2",
                        "--vocab-size",
                        "32",
                        "--hash-buckets",
                        "8",
                        "--model-width",
                        "16",
                        "--gru-hidden",
                        "8",
                        "--conv-dilations",
                        "1",
                        "--device",
                        "cpu",
                    ]
                ),
                0,
            )
            checkpoint = output / "best.pt"
            predictions = root / "predictions.jsonl"
            self.assertTrue(checkpoint.is_file())
            self.assertEqual(
                main(
                    [
                        "evaluate",
                        "--checkpoint",
                        str(checkpoint),
                        "--input",
                        str(data),
                        "--split",
                        "test",
                        "--predictions",
                        str(predictions),
                        "--device",
                        "cpu",
                    ]
                ),
                0,
            )
            self.assertTrue(predictions.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
