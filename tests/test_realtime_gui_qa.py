import unittest

from eval.realtime_gui_qa import criteria_for_profile, evaluate_realtime_gui_qa


class TestRealtimeGuiQa(unittest.TestCase):
    def diagnostics(self, **overrides):
        values = {
            "decisions_activated": 5,
            "placements_completed": 4,
            "idle_ticks": 20,
            "timeouts": 0,
            "deadline_misses": 0,
            "latency_mode": "measured",
            "mean_inference_latency_ticks": 2.0,
        }
        values.update(overrides)
        return values

    def evaluate(self, profile, **overrides):
        arguments = {
            "agents": ("player_0", "player_1"),
            "ticks": 100,
            "interrupted": False,
            "termination_reason": "game_over",
            "latency_mode": "measured",
            "controller_diagnostics": {
                "player_0": self.diagnostics(),
                "player_1": self.diagnostics(),
            },
            "attack_totals": {
                "player_0": {"generated": 1},
                "player_1": {"generated": 0},
            },
        }
        arguments.update(overrides)
        return evaluate_realtime_gui_qa(criteria_for_profile(profile), **arguments)

    def test_attack_profile_passes_with_cadence_and_generated_attack(self):
        gate = self.evaluate("attack")

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["profile"], "attack")
        self.assertEqual(gate["observed"]["generated_attack"], 1)
        self.assertEqual(gate["failure_reasons"], [])

    def test_playability_does_not_treat_tick_limit_as_completion(self):
        gate = self.evaluate("playability", termination_reason="tick_limit")

        self.assertFalse(gate["passed"])
        self.assertIn(
            "terminal_outcome_required",
            {failure["code"] for failure in gate["failure_reasons"]},
        )

    def test_attack_profile_rejects_zero_attack(self):
        gate = self.evaluate(
            "attack",
            attack_totals={
                "player_0": {"generated": 0},
                "player_1": {"generated": 0},
            },
        )

        self.assertFalse(gate["passed"])
        self.assertEqual(gate["failure_reasons"][0]["code"], "attack_not_generated")

    def test_cadence_timeout_and_latency_failures_are_machine_readable(self):
        gate = self.evaluate(
            "deterministic",
            latency_mode="measured",
            controller_diagnostics={
                "player_0": self.diagnostics(
                    decisions_activated=1,
                    placements_completed=0,
                    idle_ticks=90,
                    timeouts=1,
                    deadline_misses=1,
                ),
            },
            agents=("player_0",),
        )

        codes = {failure["code"] for failure in gate["failure_reasons"]}
        self.assertEqual(
            codes,
            {
                "latency_mode_mismatch",
                "minimum_decisions_not_met",
                "minimum_placements_not_met",
                "idle_ratio_exceeded",
                "timeout_limit_exceeded",
                "deadline_miss_limit_exceeded",
            },
        )
        self.assertEqual(gate["observed"]["agents"]["player_0"]["idle_ratio"], 0.9)


if __name__ == "__main__":
    unittest.main()
