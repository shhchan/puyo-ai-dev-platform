import tempfile
import unittest
from pathlib import Path

try:
    from agents.strategy_manager import RuleBasedManagerPolicy
    from agents.tactical_scenarios import (
        default_tactical_scenarios,
        generate_teacher_examples,
        scenario_selection_accuracy,
        write_teacher_dataset,
    )
    from agents.strategy_workers import smoke_worker_profiles

    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "tactical scenario dependencies are not installed")
class TestTacticalScenarios(unittest.TestCase):
    def test_rule_manager_selects_expected_strategy_for_fixed_suite(self):
        policy = RuleBasedManagerPolicy(smoke_worker_profiles())

        accuracy = scenario_selection_accuracy(policy)

        self.assertGreaterEqual(accuracy, 0.8)

    def test_teacher_dataset_records_all_counterfactual_workers(self):
        profiles = smoke_worker_profiles()
        examples = generate_teacher_examples(profiles=profiles)

        self.assertEqual(len(examples), len(default_tactical_scenarios()))
        self.assertTrue(all(len(example.counterfactuals) == len(profiles) for example in examples))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "teacher.json"
            write_teacher_dataset(path, examples)
            text = path.read_text(encoding="utf-8")
        self.assertIn("selected_profile_name", text)
        self.assertIn("counterfactuals", text)


if __name__ == "__main__":
    unittest.main()
