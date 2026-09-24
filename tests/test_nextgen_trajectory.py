import gzip
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents.nextgen_contracts import Diagnostics, candidate_id
from puyo_env.nextgen_public_snapshot import PublicTimingHistory
from train.artifacts import file_sha256
from train.nextgen_trajectory import (
    DecisionRecord,
    EpisodeRecord,
    iter_actor_samples,
    validate_nextgen_run,
    write_nextgen_run,
)


FIXTURES = Path(__file__).parent / "fixtures" / "nextgen"


def fixture(name="build_main"):
    return Diagnostics.from_dict(json.loads((FIXTURES / f"{name}.json").read_text()))


def provenance():
    return {
        "source": {"git_commit": "abc123", "sha256": "a" * 64},
        "native": {"wheel": "fixture.whl", "sha256": "b" * 64},
        "checkpoint": {"path": None, "sha256": None},
        "opponent": {"kind": "fixture", "manifest_sha256": None},
        "seed_streams": {"environment": 7, "search": 8, "selector": 9},
        "search_profile": {"profile_id": "smoke", "shared_quota": 10, "template_quota": 4, "response_quota": 2},
        "timing": {"schema_version": "fixture.timing.v1", "digest": "b086de7b32510701e873f3d4ae742917ca1cc1141ffe4a8387d604ef474ef9e4",
                   "latency_mode": "configured"},
        "environment": {"os": "fixture", "cpu": "fixture", "threads": 1, "p50_ms": 0.5, "p95_ms": 0.5},
        "gate_report": {"status": "fixture_only", "failures": []},
        "dataset_split": {"unit": "episode", "train": ["fixture-episode"], "validation": [], "test": []},
        "template_schema": {"schema_version": "fixture.template.v1", "digest": "c" * 64},
    }


def episode(diagnostic=None, *, status="complete"):
    d = diagnostic or fixture()
    return EpisodeRecord(
        episode_id=d.request.identity.episode_id,
        decisions=[DecisionRecord(d, {"terminal": 1.0}, 60, None, terminated=status == "complete",
                                  truncated=status == "truncated")],
        result={"status": status, "winner": 0 if status == "complete" else None,
                "end_reason": "fixture"},
        events=[{"kind": "scheduler_attempt", "tick": 100}],
        private_reproduction={"hidden_seed": 123}, oracle={"future": [1, 2, 3]},
        actual_metrics={"chains": 0, "attack": 0, "canceled": 0, "received": 0, "survival_ticks": 60},
        public_timing_history=PublicTimingHistory((), ()),
    )


def next_diagnostic(previous):
    old = previous.batch.candidates[0]
    identity = replace(previous.request.identity, decision_id=8, request_id="fixture-request-8")
    new_id = candidate_id(identity, old.plan, old.assumptions)
    candidate = replace(old, identity=identity, candidate_id=new_id)
    tactics = tuple(replace(row,
                            candidate_ids=(new_id,) if row.candidate_ids else (),
                            best_id=new_id if row.best_id else None)
                    for row in previous.batch.tactics)
    batch = replace(previous.batch, identity=identity, candidates=(candidate,), tactics=tactics)
    return replace(previous,
                   request=replace(previous.request, identity=identity),
                   batch=batch,
                   selection=replace(previous.selection, candidate_id=new_id, batch_digest=batch.digest),
                   receipt=replace(previous.receipt, requested_candidate_id=new_id))


class NextgenTrajectoryTest(unittest.TestCase):
    def produce(self, path, ep=None):
        return write_nextgen_run(run_dir=path, run_id="fixture-run", episodes=[ep or episode()],
                                 config={"profile": "smoke"}, provenance=provenance(), git_commit="abc123")

    def replace_artifact(self, root, manifest, role, mutation):
        record = next(r for r in manifest["artifacts"] if r["role"] == role)
        path = Path(root) / record["path"]
        if path.suffix == ".gz":
            with gzip.open(path, "rt") as handle:
                rows = [json.loads(line) for line in handle]
            mutation(rows)
            with gzip.open(path, "wt") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
        else:
            value = json.loads(path.read_text())
            mutation(value)
            path.write_text(json.dumps(value))
        record["sha256"] = file_sha256(path)
        record["size_bytes"] = path.stat().st_size
        (Path(root) / "artifact_manifest.json").write_text(json.dumps(manifest))

    def test_real_policy_input_and_isolated_training_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.produce(tmp)
            rows = list(iter_actor_samples(tmp))
            self.assertEqual(rows, [(fixture().features.to_dict(), "build_main")])
            self.assertNotIn("hidden_seed", json.dumps(rows))
            self.assertNotIn("future", json.dumps(rows))
            self.assertEqual(manifest["extra"]["nextgen"]["provenance"]["seed_streams"]["search"], 8)
            roles = {r["role"]: r for r in manifest["artifacts"]}
            self.assertTrue(all("sha256" in r and "size_bytes" in r for r in roles.values()))
            self.assertNotEqual(roles["fixture-episode_private"]["path"], roles["fixture-episode_trajectory"]["path"])
            self.assertEqual(validate_nextgen_run(tmp), manifest)

    def test_private_future_variation_cannot_change_actor_sample(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self.produce(first, episode())
            changed = replace(episode(), private_reproduction={"hidden_seed": 999},
                              oracle={"future": [4, 3, 2]})
            self.produce(second, changed)
            self.assertEqual(list(iter_actor_samples(first)), list(iter_actor_samples(second)))

    def test_two_decisions_join_next_result_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = fixture()
            second = next_diagnostic(first)
            ep = replace(episode(first), decisions=[
                DecisionRecord(first, {"potential": 0.25}, 30, 8),
                DecisionRecord(second, {"terminal": 1.0}, 60, None, terminated=True),
            ])
            self.produce(tmp, ep)
            self.assertEqual([target for _, target in iter_actor_samples(tmp)], ["build_main", "build_main"])
            self.assertEqual(len(validate_nextgen_run(tmp)["extra"]["nextgen"]["episode_ids"]), 1)

    def test_partial_is_separate_from_episode_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.produce(tmp, episode(fixture("partial"), status="truncated"))
            summary = Path(tmp) / next(r["path"] for r in manifest["artifacts"] if r["role"] == "fixture-episode_summary")
            value = json.loads(summary.read_text())
            self.assertEqual(value["partial_batch_count"], 1)
            self.assertEqual(value["result"]["status"], "truncated")
            self.assertIsNone(value["result"]["winner"])

        with tempfile.TemporaryDirectory() as tmp:
            ep = replace(episode(status="incomplete"),
                         decisions=[DecisionRecord(fixture(), {"observed": 0.0}, 12, None)])
            self.produce(tmp, ep)
            self.assertEqual(validate_nextgen_run(tmp)["extra"]["nextgen"]["episode_ids"],
                             ["fixture-episode"])

    def test_fallback_excluded_but_transition_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.produce(tmp, episode(fixture("fallback")))
            self.assertEqual(list(iter_actor_samples(tmp)), [])
            path = Path(tmp) / next(r["path"] for r in manifest["artifacts"] if r["role"] == "fixture-episode_trajectory")
            with gzip.open(path, "rt") as source:
                row = json.loads(source.readline())
            self.assertEqual(row["transition"]["actor_exclusion_reason"], "fallback")
            self.assertEqual(row["transition"]["reward_components"], {"terminal": 1.0})
            self.assertEqual(row["execution"]["receipt"]["executed_action"], 4)

    def test_corrupt_sha_or_byte_count_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.produce(tmp)
            path = Path(tmp) / manifest["artifacts"][0]["path"]
            path.write_bytes(path.read_bytes() + b"bad")
            with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                validate_nextgen_run(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.produce(tmp)
            manifest["artifacts"][0]["size_bytes"] += 1
            (Path(tmp) / "artifact_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "byte count mismatch"):
                validate_nextgen_run(tmp)

    def test_missing_sidecar_reference_duplicate_and_old_schema_rejected(self):
        for mutation, message in (("missing", "missing next decision"),
                                  ("duplicate", "duplicate/mismatched decision"),
                                  ("schema", "old/unknown trajectory schema")):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                manifest = self.produce(tmp)
                record = next(r for r in manifest["artifacts"] if r["role"] == "fixture-episode_trajectory")
                path = Path(tmp) / record["path"]
                with gzip.open(path, "rt") as handle:
                    row = json.loads(handle.readline())
                if mutation == "missing":
                    row["transition"]["next_decision_key"] = "fixture-run/fixture-episode/0/999"
                    rows = [row]
                elif mutation == "duplicate":
                    rows = [row, row]
                    summary = next(r for r in manifest["artifacts"] if r["role"] == "fixture-episode_summary")
                    summary_path = Path(tmp) / summary["path"]
                    content = json.loads(summary_path.read_text())
                    content["decision_count"] = 2
                    summary_path.write_text(json.dumps(content))
                    summary["sha256"] = file_sha256(summary_path)
                    summary["size_bytes"] = summary_path.stat().st_size
                else:
                    row["schema_version"] = "puyo.nextgen.trajectory.v0"
                    rows = [row]
                with gzip.open(path, "wt") as handle:
                    for item in rows:
                        handle.write(json.dumps(item) + "\n")
                record["sha256"] = file_sha256(path)
                record["size_bytes"] = path.stat().st_size
                (Path(tmp) / "artifact_manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, message):
                    validate_nextgen_run(tmp)

    def test_seed_stream_collision_and_incomplete_loss_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = provenance()
            bad["seed_streams"]["search"] = 7
            with self.assertRaisesRegex(ValueError, "independent"):
                write_nextgen_run(run_dir=tmp, run_id="fixture-run", episodes=[episode()],
                                  config={}, provenance=bad, git_commit="abc123")
        with tempfile.TemporaryDirectory() as tmp:
            bad_ep = replace(episode(status="incomplete"), result={"status": "incomplete", "winner": 1,
                                                                   "end_reason": "disconnect"})
            with self.assertRaisesRegex(ValueError, "cannot imply a loss"):
                self.produce(tmp, bad_ep)

    def test_rehashed_semantic_corruption_rejected(self):
        cases = [
            ("fixture-episode_trajectory", lambda rows: rows[0]["transition"]["reward_components"].update(terminal=1e309), "nonfinite JSON"),
            ("fixture-episode_trajectory", lambda rows: rows[0]["transition"].update(next_decision_key="fixture-run/fixture-episode/0/7"), "invalid next decision order/player"),
            ("fixture-episode_trajectory", lambda rows: rows[0]["decision"].update(phase_before={}), "phase before mismatch"),
            ("feature_schema", lambda value: value["registry"][0].update(scale=999), "registry snapshot mismatch"),
            ("fixture-episode_summary", lambda value: value["result"].update(status="truncated", winner=None), "episode result/terminal transition mismatch"),
        ]
        for role, mutation, message in cases:
            with self.subTest(role=role, message=message), tempfile.TemporaryDirectory() as tmp:
                manifest = self.produce(tmp)
                self.replace_artifact(tmp, manifest, role, mutation)
                with self.assertRaisesRegex(ValueError, message):
                    validate_nextgen_run(tmp)

    def test_duplicate_episode_id_and_unlisted_episode_artifact_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.produce(tmp)
            manifest["extra"]["nextgen"]["episode_ids"].append("fixture-episode")
            (Path(tmp) / "artifact_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "duplicate/empty episode IDs"):
                validate_nextgen_run(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.produce(tmp)
            manifest["extra"]["nextgen"]["episode_ids"] = []
            (Path(tmp) / "artifact_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "duplicate/empty episode IDs"):
                validate_nextgen_run(tmp)

    def test_stale_timeout_and_fallback_are_not_actor_samples(self):
        for outcome in ("stale", "timeout", "fallback"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                d = fixture()
                receipt = replace(d.receipt, outcome=outcome, reason=f"fixture_{outcome}",
                                  pre_execution_snapshot_digest="f" * 64)
                changed = replace(d, receipt=receipt)
                self.produce(tmp, episode(changed))
                self.assertEqual(list(iter_actor_samples(tmp)), [])


if __name__ == "__main__":
    unittest.main()
