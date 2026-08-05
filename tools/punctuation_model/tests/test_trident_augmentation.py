from __future__ import annotations

import random
import unittest

from tools.punctuation_model.trident_augmentation import TridentTextNoiseMixer


class TridentTextNoiseTests(unittest.TestCase):
    def test_all_branches_preserve_punctuation_and_quotes(self) -> None:
        mixer = TridentTextNoiseMixer(
            weights={
                "clean": 1,
                "homophonic": 1,
                "chinese": 1,
                "bopomofo": 1,
                "homomorphic": 1,
            },
            bopomofo_convert_ratio=0.2,
            homophonic_convert_ratio=0.1,
            homomorphic_convert_ratio=0.02,
        )
        source = "他說：「今天天氣很好。」"
        seen: set[str] = set()
        for seed in range(100):
            output, branch = mixer.apply(source, random.Random(seed))
            seen.add(branch)
            self.assertEqual(
                [char for char in output if char in "：「」。"],
                [char for char in source if char in "：「」。"],
            )
        self.assertEqual(
            seen,
            {"clean", "homophonic", "chinese", "bopomofo", "homomorphic"},
        )


if __name__ == "__main__":
    unittest.main()
