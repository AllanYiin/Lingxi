from __future__ import annotations

import unittest

import torch

from tools.punctuation_model.data import (
    CharVocabulary,
    collate_batch,
    encode_example,
    extract_labeled_text,
)
from tools.punctuation_model.decoding import constrained_decode, render_prediction
from tools.punctuation_model.labels import PunctuationLabel, QuoteLabel
from tools.punctuation_model.model import ModelConfig, TinyPunctuationModel


class LabelExtractionTests(unittest.TestCase):
    def test_extracts_punctuation_and_balanced_quotes_at_boundaries(self) -> None:
        example = extract_labeled_text({"text": "他說：「你好。」"})
        self.assertEqual(example.units, ("他", "說", "你", "好"))
        self.assertEqual(
            example.punctuation,
            (
                PunctuationLabel.NONE,
                PunctuationLabel.NONE,
                PunctuationLabel.COLON,
                PunctuationLabel.NONE,
                PunctuationLabel.PERIOD,
            ),
        )
        self.assertEqual(
            example.quotes,
            (
                QuoteLabel.NONE,
                QuoteLabel.NONE,
                QuoteLabel.OPEN,
                QuoteLabel.NONE,
                QuoteLabel.CLOSE,
            ),
        )

    def test_preserves_protected_url_and_decimal_punctuation(self) -> None:
        example = extract_labeled_text(
            {"text": "請看 https://example.com，價格是 12.5 元。"}
        )
        plain = "".join(example.units)
        self.assertIn("https://example.com", plain)
        self.assertIn("12.5", plain)
        self.assertEqual(example.punctuation.count(PunctuationLabel.COMMA), 1)
        self.assertEqual(example.punctuation.count(PunctuationLabel.PERIOD), 1)

    def test_rejects_unclosed_quotes(self) -> None:
        with self.assertRaisesRegex(ValueError, "not closed"):
            extract_labeled_text({"text": "他說：「你好。"})


class DecoderTests(unittest.TestCase):
    def test_forces_balanced_quote_path(self) -> None:
        punctuation = torch.zeros(4, 8)
        quotes = torch.tensor(
            [
                [4.0, 0.0, 0.0],
                [0.0, 5.0, 0.0],
                [0.0, 4.0, 3.0],
                [0.0, 5.0, 4.0],
            ]
        )
        decoded = constrained_decode(
            punctuation,
            quotes,
            torch.zeros(8, 3),
            [False, True, True, True],
        )
        self.assertEqual(decoded.quotes[1], QuoteLabel.OPEN)
        self.assertEqual(decoded.quotes[3], QuoteLabel.CLOSE)
        self.assertEqual(
            render_prediction(
                ["你", "好", "嗎"],
                decoded.punctuation,
                decoded.quotes,
                style="zh-tw",
            ),
            "你「好嗎」",
        )


class ModelTests(unittest.TestCase):
    def test_default_model_is_under_two_hundred_thousand_parameters(self) -> None:
        model = TinyPunctuationModel(ModelConfig(vocab_size=4096))
        self.assertLess(model.parameter_count(), 200_000)

    def test_forward_and_loss(self) -> None:
        first = extract_labeled_text({"text": "你好。"})
        second = extract_labeled_text({"text": "他問：「好嗎？」"})
        vocabulary = CharVocabulary(
            ("<PAD>", "<UNK>", "<BOS>", "你", "好", "他", "問", "嗎")
        )
        batch = collate_batch(
            [
                encode_example(first, vocabulary, 16),
                encode_example(second, vocabulary, 16),
            ]
        )
        model = TinyPunctuationModel(
            ModelConfig(vocab_size=len(vocabulary.id_to_char), hash_buckets=16)
        )
        outputs = model(batch)
        self.assertEqual(outputs["punctuation_logits"].shape[:2], batch["char_ids"].shape)
        self.assertEqual(outputs["punctuation_logits"].shape[-1], 8)
        self.assertEqual(outputs["quote_logits"].shape[-1], 3)
        losses = model.loss(outputs, batch["punctuation"], batch["quotes"])
        self.assertTrue(torch.isfinite(losses["loss"]))
        self.assertTrue(torch.isfinite(losses["event_loss"]))
        self.assertTrue(torch.isfinite(losses["positive_pair_loss"]))


if __name__ == "__main__":
    unittest.main()
