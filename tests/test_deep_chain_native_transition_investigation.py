import hashlib
import tempfile
import unittest
from pathlib import Path

from eval.deep_chain_native_transition_investigation import (
    AUTHORITATIVE_RUN_COUNT,
    DEFAULT_OUTPUT_DIR,
    OUTCOME_ORDER,
    _locked_contract,
    _parse_candidate_output,
    _summarize_outcome_runs,
    _validate_locked_contract,
    _write_candidate_workload,
    parse_args,
    verify_investigation,
)


def _synthetic_run(index: int) -> dict:
    outcomes = {}
    for name in OUTCOME_ORDER:
        base = {
            "mixed": 70.0,
            "quiet": 40.0,
            "one_chain": 250.0,
            "multi_chain": 320.0,
        }[name]
        outcomes[name] = {
            "summary": {
                "p50_ns_per_record": base - 5.0 + index,
                "p95_ns_per_record": base + index,
            }
        }
    return {"outcomes": outcomes}


class TestDeepChainNativeTransitionInvestigation(unittest.TestCase):
    def test_puyo_207_contract_is_locked(self):
        contract = _locked_contract()

        self.assertEqual(_validate_locked_contract(contract), [])
        self.assertEqual(contract["mixed_samples"], 120)
        self.assertEqual(contract["outcome_samples"], 40)
        self.assertEqual(contract["warmup_samples_per_mode"], 5)
        self.assertEqual(contract["targets"]["quiet_p95_ns_per_transition"], 50.0)

    def test_contract_validator_detects_post_hoc_target_change(self):
        contract = _locked_contract()
        contract["targets"]["quiet_p95_ns_per_transition"] = 60.0

        issues = _validate_locked_contract(contract)

        self.assertIn("locked target changed quiet_p95_ns_per_transition", issues)
        self.assertIn("locked contract digest is invalid", issues)

    def test_three_run_summary_uses_nearest_rank_without_exclusion(self):
        runs = [_synthetic_run(index) for index in range(AUTHORITATIVE_RUN_COUNT)]

        result = _summarize_outcome_runs(runs)

        self.assertEqual(result["quiet"]["p95_ns_per_record_by_run"], [40.0, 41.0, 42.0])
        self.assertEqual(result["quiet"]["median_run_p95_ns_per_record"], 41.0)
        self.assertTrue(
            result["performance_gates"]["quiet_all_runs_at_or_below_50_ns"]
        )
        self.assertTrue(
            result["performance_gates"][
                "quiet_median_run_p95_at_or_below_45_ns"
            ]
        )

    def test_candidate_output_parser_preserves_ab_ba_samples(self):
        output = (
            "metadata\t1\t4096\t128\t5\t0\n"
            "sample\t0\tAB\t100\t200\t80\t160\t42\n"
            "sample\t1\tBA\t110\t220\t90\t180\t43"
        )

        metadata, rows = _parse_candidate_output(output)

        self.assertEqual(metadata["semantic_mismatch_count"], 0)
        self.assertEqual([row["order"] for row in rows], ["AB", "BA"])

    def test_candidate_workload_uses_locked_quiet_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.bin"

            result = _write_candidate_workload(
                "eval/deep_chain_native_transition_corpus.json", path
            )

            self.assertEqual(result["record_count"], 4_096)
            self.assertEqual(result["selection"]["measured_records"], 4_096)
            self.assertEqual(
                result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
            )

    def test_cli_requires_explicit_valgrind_for_canonical_run(self):
        args = parse_args(["run", "--valgrind", "/tmp/valgrind"])

        self.assertEqual(args.cpu, 0)
        self.assertEqual(args.valgrind, "/tmp/valgrind")

    @unittest.skipUnless(DEFAULT_OUTPUT_DIR.exists(), "PUYO-211 evidence is not checked in")
    def test_checked_in_investigation_is_integral(self):
        result = verify_investigation()

        self.assertTrue(result["passed"], result["issues"])


if __name__ == "__main__":
    unittest.main()
