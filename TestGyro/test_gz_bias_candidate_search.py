import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("t9_gz_bias_candidate_search.py")
SPEC = importlib.util.spec_from_file_location("t9_gz_bias_candidate_search", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
search = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search)


class CandidateSearchAnalysisTests(unittest.TestCase):
    def test_summary_combines_mean_and_variance_in_rms(self):
        result = search.summarize_candidate([-0.03, 0.03])
        self.assertAlmostEqual(result["signed_mean_m"], 0.0)
        self.assertAlmostEqual(result["rms_lateral_error_m"], 0.03)
        self.assertGreater(result["sample_standard_deviation_m"], 0.03)

    def test_clearly_degraded_outer_candidate_is_obviously_worse(self):
        self.assertTrue(
            search.obviously_worse(
                outer_values=[-0.06, -0.07],
                inward_values=[-0.01, 0.01],
            )
        )

    def test_small_rms_difference_is_not_obviously_worse(self):
        self.assertFalse(
            search.obviously_worse(
                outer_values=[-0.03, -0.04],
                inward_values=[-0.02, 0.02],
            )
        )

    def test_best_pair_must_be_neighboring(self):
        candidates = {
            -2: [-0.01, 0.01],
            -1: [-0.02, 0.02],
            0: [-0.08, 0.08],
            1: [-0.03, 0.03],
        }
        left, right, _scores = search.choose_best_neighboring_pair(candidates)
        self.assertEqual((left, right), (-2, -1))

    def test_final_selection_uses_lower_rms_when_not_tied(self):
        selected, reason = search.choose_final_candidate(
            -1,
            [-0.01, 0.01, -0.01, 0.01],
            0,
            [-0.05, 0.05, -0.04, 0.04],
        )
        self.assertEqual(selected, -1)
        self.assertEqual(reason, "LOWEST_RMS")

    def test_rms_tie_prefers_candidate_closer_to_static_center(self):
        selected, reason = search.choose_final_candidate(
            -1,
            [-0.02, 0.02, -0.02, 0.02],
            0,
            [-0.02, 0.02, -0.02, 0.02],
        )
        self.assertEqual(selected, 0)
        self.assertEqual(reason, "RMS_TIE_BREAK")


if __name__ == "__main__":
    unittest.main()
