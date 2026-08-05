from __future__ import annotations

import unittest

from tools.punctuation_model.metrics import EvaluationAccumulator


class NonNoneMetricTests(unittest.TestCase):
    def test_joint_metric_excludes_empty_boundaries(self) -> None:
        metrics = EvaluationAccumulator()
        metrics.update(
            punctuation_targets=[0, 0, 1, 2],
            quote_targets=[0, 0, 0, 2],
            punctuation_predictions=[0, 1, 1, 0],
            quote_predictions=[0, 0, 0, 2],
            greedy_was_unclosed=False,
        )
        report = metrics.report()
        self.assertEqual(report["all_boundary_accuracy"], 0.5)
        self.assertEqual(report["joint_non_none_accuracy"], 0.5)
        self.assertEqual(report["false_insertion_rate"], 0.5)
        self.assertEqual(report["non_none_boundaries"], 2)
        self.assertEqual(report["empty_boundaries"], 2)
        self.assertAlmostEqual(report["event_detection"]["precision"], 2 / 3)
        self.assertEqual(report["event_detection"]["recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
