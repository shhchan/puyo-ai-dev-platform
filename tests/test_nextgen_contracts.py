"""Public contract acceptance and adversarial serialization tests (no search)."""

import json
import math
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from agents import nextgen_contracts as c

FIXTURES = Path(__file__).parent / "fixtures" / "nextgen"


def make_diagnostics(
    tactic="build_main", *, partial=False, fallback=False, unavailable=False
):
    """Synthetic producer: contract examples do not assert search capability."""
    h = c.semantic_digest("fixture")
    player = c.PublicPlayerState(
        tuple((0,) * 6 for _ in range(12)),
        ((1, 2), (2, 3), (3, 4)),
        "falling",
        (),
        0,
        False,
        False,
        False,
    )
    public = c.PublicSnapshot(player, player, ())
    identity = c.DecisionIdentity(
        "fixture-episode", 0, 7, "fixture-request-7", public.digest
    )
    phase = c.PhaseSnapshot(
        "phase-0", "gtr", True, 8, 14, 0.4, "fit", "build_template", 6
    )
    profile = c.SearchProfile("smoke", 10, 4, 2)
    request = c.NextgenRequest(
        identity,
        public,
        c.ExecutionContext(
            (not unavailable,) * c.NUM_ACTIONS,
            100,
            110,
            "fixture.timing.v1",
            h,
            "configured",
        ),
        c.ControlContext(
            phase,
            profile,
            h,
            c.ScenarioProvenance(
                "fixture.v1", c.semantic_digest(player.known_pieces), "search", h
            ),
        ),
    )
    assumptions = c.CandidateAssumptions(
        public.digest, h, h, c.semantic_digest(profile), h
    )
    plan = (c.PlanStep(4, (1, 2), "public_known"),)
    evidence = (
        c.NamedEvidence(
            "outgoing", c.NumericEvidence(None, "not_evaluated", "public_estimate")
        ),
        c.NamedEvidence(
            "scenario_coverage",
            c.NumericEvidence(
                0.5 if partial else 1.0,
                "partial" if partial else "evaluated",
                "sampled_future",
            ),
        ),
    )
    tactics = ("build_main",) if tactic == "build_main" else ("build_main", tactic)
    candidate = c.Candidate(
        identity,
        c.candidate_id(identity, plan, assumptions),
        4,
        plan,
        tactics,
        0,
        True,
        True,
        assumptions,
        evidence,
        fallback,
    )
    candidates = () if unavailable else (candidate,)
    rows = tuple(
        c.TacticSummary(
            t,
            (candidate.candidate_id,) if t in tactics and not unavailable else (),
            candidate.candidate_id if t in tactics and not unavailable else None,
            t in tactics and not unavailable,
            "available"
            if t in tactics and not unavailable
            else "not_evaluated"
            if unavailable
            else "not_found_within_budget",
            "not_evaluated" if unavailable else "partial" if partial else "evaluated",
            t in tactics and not unavailable and not fallback,
            evidence,
        )
        for t in c.TACTIC_IDS
    )
    batch = c.CandidateBatch(
        identity,
        "unavailable" if unavailable else "partial" if partial else "complete",
        "no_reachable_root" if unavailable else "node_quota" if partial else None,
        3,
        candidates,
        rows,
        c.SearchCounters(2, 1, 0, 3, 0.5),
    )
    summary = {
        "threat.none": 1,
        "response.unknown": 1,
        "phase.remaining_ratio": 8 / 14,
        "own.main.firepower": 360,
        "first_fire_loss.upper": 6,
    }
    for row in rows:
        summary[f"tactic.{row.tactic_id}.available"] = float(row.available)
        summary[f"tactic.{row.tactic_id}.count"] = len(row.candidate_ids)
        summary[f"tactic.{row.tactic_id}.known_witness"] = float(row.known_witness)
    features = c.build_features(summary, batch.action_mask)
    selection = (
        None
        if unavailable
        else c.Selection(
            tactic,
            candidate.candidate_id,
            batch.digest,
            "rule",
            None,
            None,
            None,
            "fixture",
        )
    )
    receipt = (
        None
        if unavailable
        else c.ExecutionReceipt(
            candidate.candidate_id,
            4,
            4,
            "fallback" if fallback else "activated",
            100,
            101,
            102,
            110,
            public.digest,
            "fixture_fallback" if fallback else "fixture_activation",
        )
    )
    return c.Diagnostics(request, batch, features, selection, receipt)


class NextgenContractTests(unittest.TestCase):
    def test_checked_in_roundtrip_fixtures_cover_six_tactics_partial_fallback(self):
        expected = {*c.TACTIC_IDS, "partial", "fallback", "unavailable"}
        paths = list(FIXTURES.glob("*.json"))
        self.assertEqual({p.stem for p in paths}, expected | {"feature_registry"})
        for path in paths:
            if path.stem == "feature_registry":
                continue
            with self.subTest(path=path):
                raw = json.loads(path.read_text())
                diag = c.from_dict(raw)
                self.assertEqual(diag, c.from_json(diag.to_json()))
                self.assertEqual(raw, diag.to_dict())
                for item in (diag.request, diag.batch, diag.features, diag.selection):
                    if item is not None:
                        self.assertEqual(item, c.from_json(item.to_json()))
                if path.stem in c.TACTIC_IDS:
                    self.assertEqual(diag.selection.selected_tactic_id, path.stem)
        self.assertFalse(
            c.from_dict(
                json.loads((FIXTURES / "fallback.json").read_text())
            ).receipt.actor_trainable
        )

    def test_fixture_producer_matches_committed_payloads(self):
        for tactic in c.TACTIC_IDS:
            self.assertEqual(
                make_diagnostics(tactic).to_dict(),
                json.loads((FIXTURES / f"{tactic}.json").read_text()),
            )
        for variant in ("partial", "fallback", "unavailable"):
            self.assertEqual(
                make_diagnostics(**{variant: True}).to_dict(),
                json.loads((FIXTURES / f"{variant}.json").read_text()),
            )

    def test_feature_registry_is_ordered_and_hashed_with_all_metadata(self):
        golden = json.loads((FIXTURES / "feature_registry.json").read_text())
        self.assertEqual(golden["hash"], c.FEATURE_REGISTRY_HASH)
        self.assertEqual(golden["features"], [s.to_dict() for s in c.FEATURE_REGISTRY])
        self.assertEqual(len(c.FEATURE_NAMES), len(set(c.FEATURE_NAMES)))
        changed = [s.to_dict() for s in c.FEATURE_REGISTRY]
        changed[0]["scale"] = 2
        self.assertNotEqual(
            c.semantic_digest(
                {"schema_version": c.FEATURE_SCHEMA_VERSION, "features": changed}
            ),
            c.FEATURE_REGISTRY_HASH,
        )
        self.assertNotEqual(
            c.semantic_digest(
                {"schema_version": c.FEATURE_SCHEMA_VERSION, "features": changed[::-1]}
            ),
            c.FEATURE_REGISTRY_HASH,
        )
        self.assertEqual(
            c.TACTIC_IDS,
            (
                "build_main",
                "build_template",
                "fire_main",
                "cancel",
                "counter",
                "decisive_short_attack",
            ),
        )

    def test_missingness_normalization_and_actor_allowlist(self):
        features = c.build_features(
            {
                "own.main.firepower": 360,
                "own.main.chain_count": 38,
                "first_fire_loss.upper": 9,
                "phase.continuation": 7,
                "phase.progress": c.NumericEvidence(0.25, "partial", "public_estimate"),
            },
            (True,) * 6,
        )
        for name, expected in (
            ("own.main.firepower", 1),
            ("own.main.chain_count", 1),
            ("first_fire_loss.upper", 1),
            ("phase.continuation", 0.5),
            ("phase.progress", 0.25),
        ):
            self.assertEqual(features.values[c.FEATURE_NAMES.index(name)], expected)
            self.assertFalse(features.missing[c.FEATURE_NAMES.index(name)])
        self.assertTrue(features.missing[0])
        self.assertEqual(features.values[0], 0)
        self.assertEqual(len(features.actor_vector), len(c.FEATURE_NAMES) * 2)
        for name in (
            "board",
            "raw_tick",
            "action_sequence",
            "seed",
            "scenario_id",
            "future_sequence",
            "path",
            "snapshot_digest",
            "template_id",
            "own.board",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                c.build_features({name: 1}, (True,) * 6)
        raw = features.to_dict()
        raw["raw_board"] = [[0]]
        with self.assertRaises(ValueError):
            c.PolicyFeatures.from_dict(raw)

    def test_reject_unknown_missing_schema_fields_and_legacy(self):
        for obj in (
            make_diagnostics().request,
            make_diagnostics().batch,
            make_diagnostics().features,
            make_diagnostics().selection,
            make_diagnostics(),
        ):
            for version in (
                None,
                "puyo.worker_proposal_batch.v2",
                "puyo.nextgen.features.v2",
            ):
                raw = obj.to_dict()
                raw["schema_version"] = version
                with (
                    self.subTest(schema=version, obj=type(obj)),
                    self.assertRaises(ValueError),
                ):
                    c.from_dict(raw)
            raw = obj.to_dict()
            del raw["schema_version"]
            with self.assertRaises(ValueError):
                type(obj).from_dict(raw)
        with self.assertRaises(ValueError):
            c.from_json('{"schema_version":"a","schema_version":"b"}')

    def test_nonfinite_numbers_and_type_coercion_are_rejected(self):
        for value in (math.nan, math.inf, -math.inf, True, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                c.NumericEvidence(value, "evaluated", "visible_exact")
            with self.assertRaises(ValueError):
                c.build_features({"phase.progress": value}, (True,) * 6)
        for field in ("elapsed_ms",):
            with self.assertRaises(ValueError):
                replace(make_diagnostics().batch.counters, **{field: math.nan})
        for status, value in (
            ("evaluated", None),
            ("unsupported", 0),
            ("not_evaluated", 1),
        ):
            with self.assertRaises(ValueError):
                c.NumericEvidence(value, status, "visible_exact")
        with self.assertRaises(ValueError):
            replace(make_diagnostics().features, action_mask=(1,) * 6)
        with self.assertRaises(ValueError):
            c.from_json('{"schema_version":"puyo.nextgen.features.v1","values":[NaN]}')

    def test_candidate_identity_is_stable_semantic_and_telemetry_independent(self):
        diag = make_diagnostics()
        candidate = diag.batch.candidates[0]
        self.assertEqual(
            c.candidate_id(
                replace(candidate.identity, request_id="retry"),
                candidate.plan,
                candidate.assumptions,
            ),
            candidate.candidate_id,
        )
        self.assertNotEqual(
            c.candidate_id(
                replace(candidate.identity, decision_id=8),
                candidate.plan,
                candidate.assumptions,
            ),
            candidate.candidate_id,
        )
        self.assertNotEqual(
            c.candidate_id(
                candidate.identity,
                (replace(candidate.plan[0], action=5),),
                candidate.assumptions,
            ),
            candidate.candidate_id,
        )
        self.assertNotEqual(
            c.candidate_id(
                candidate.identity,
                candidate.plan,
                replace(candidate.assumptions, timing_digest=c.semantic_digest("new")),
            ),
            candidate.candidate_id,
        )
        self.assertEqual(
            diag.batch.digest,
            replace(
                diag.batch, counters=replace(diag.batch.counters, elapsed_ms=999)
            ).digest,
        )
        with self.assertRaises(ValueError):
            replace(candidate, candidate_id="t7")
        with self.assertRaises(ValueError):
            replace(candidate, root_action=-1)
        with self.assertRaises(ValueError):
            replace(candidate, root_action=True)
        with self.assertRaises(ValueError):
            replace(candidate, root_action=c.NUM_ACTIONS)
        with self.assertRaises(ValueError):
            replace(candidate, tactics=("survive",))

    def test_candidate_references_masks_and_ranks_are_checked(self):
        diag = make_diagnostics()
        batch = diag.batch
        with self.assertRaises(ValueError):
            replace(batch, tactics=batch.tactics[::-1])
        with self.assertRaises(ValueError):
            replace(batch, candidates=batch.candidates * 2)
        with self.assertRaises(ValueError):
            replace(batch.tactics[0], best_id="other")
        with self.assertRaises(ValueError):
            replace(batch.tactics[0], mask_reason="not_found_within_budget")
        foreign = "candidate:" + c.semantic_digest("foreign")
        row = replace(batch.tactics[0], candidate_ids=(foreign,), best_id=foreign)
        with self.assertRaises(ValueError):
            replace(batch, tactics=(row,) + batch.tactics[1:])
        for selection in (
            replace(diag.selection, selected_tactic_id="counter"),
            replace(diag.selection, candidate_id=foreign),
            replace(diag.selection, batch_digest=c.semantic_digest("stale")),
        ):
            with self.assertRaises(ValueError):
                replace(diag, selection=selection)
        with self.assertRaises(ValueError):
            replace(diag, features=replace(diag.features, action_mask=(True,) * 6))
        with self.assertRaises(ValueError):
            replace(diag, receipt=replace(diag.receipt, requested_candidate_id=foreign))

    def test_request_assumptions_quota_public_prefix_and_fallback(self):
        diag = make_diagnostics()
        for request in (
            replace(
                diag.request,
                execution=replace(
                    diag.request.execution, reachable_mask=(False,) * c.NUM_ACTIONS
                ),
            ),
            replace(
                diag.request,
                execution=replace(
                    diag.request.execution, timing_digest=c.semantic_digest("other")
                ),
            ),
            replace(
                diag.request,
                control=replace(
                    diag.request.control,
                    search_profile=c.SearchProfile("smoke", 0, 0, 0),
                ),
            ),
        ):
            with self.assertRaises(ValueError):
                c.validate_request_batch(request, diag.batch)
        with self.assertRaises(ValueError):
            replace(diag.batch, known_prefix_length=0)
        empty = make_diagnostics(unavailable=True)
        with self.assertRaises(ValueError):
            c.validate_request_batch(diag.request, empty.batch)
        with self.assertRaises(ValueError):
            replace(
                diag.request,
                identity=replace(
                    diag.request.identity, snapshot_digest=c.semantic_digest("wrong")
                ),
            )

    def test_frozen_public_boundary_and_private_fields(self):
        diag = make_diagnostics()
        raw = diag.request.to_dict()
        request = c.NextgenRequest.from_dict(raw)
        raw["public"]["own"]["visible_board"][0][0] = 2
        self.assertEqual(request.public.own.visible_board[0][0], 0)
        with self.assertRaises(FrozenInstanceError):
            request.public.own.phase = "other"
        for field in ("seed", "private_queue", "simulator", "future_sequence"):
            payload = request.to_dict()
            payload["public"]["own"][field] = 1
            with self.assertRaises(ValueError):
                c.NextgenRequest.from_dict(payload)
        with self.assertRaises(ValueError):
            replace(request.public.own, known_pieces=((1, 2),) * 4)
        with self.assertRaises(ValueError):
            replace(request.public, information_mode="public_reconstructed")

    def test_second_rank_and_wrong_known_piece_are_rejected(self):
        diag = make_diagnostics()
        first = diag.batch.candidates[0]
        plan = (replace(first.plan[0], action=5),)
        second = replace(
            first,
            plan=plan,
            root_action=5,
            rank=1,
            candidate_id=c.candidate_id(first.identity, plan, first.assumptions),
        )
        row = replace(
            diag.batch.tactics[0],
            candidate_ids=(first.candidate_id, second.candidate_id),
        )
        batch = replace(
            diag.batch,
            candidates=(first, second),
            tactics=(row,) + diag.batch.tactics[1:],
        )
        with self.assertRaises(ValueError):
            replace(
                diag.selection,
                candidate_id=second.candidate_id,
                batch_digest=batch.digest,
            ).validate_batch(batch)
        wrong_plan = (replace(first.plan[0], piece=(4, 4)),)
        wrong = replace(
            first,
            plan=wrong_plan,
            candidate_id=c.candidate_id(first.identity, wrong_plan, first.assumptions),
        )
        row = replace(
            diag.batch.tactics[0],
            candidate_ids=(wrong.candidate_id,),
            best_id=wrong.candidate_id,
        )
        batch = replace(
            diag.batch, candidates=(wrong,), tactics=(row,) + diag.batch.tactics[1:]
        )
        with self.assertRaises(ValueError):
            c.validate_request_batch(diag.request, batch)

    def test_wire_colors_and_configurable_carry(self):
        from src.core.constants import NORMAL_PUYO_COLORS, PuyoColor

        self.assertEqual(
            tuple(c.PUBLIC_CELL_TO_COLOR[i] for i in c.PUBLIC_COLOR_IDS),
            NORMAL_PUYO_COLORS,
        )
        self.assertIs(c.PUBLIC_CELL_TO_COLOR[c.PUBLIC_GARBAGE_ID], PuyoColor.OJAMA)
        self.assertIs(c.PUBLIC_CELL_TO_COLOR[0], PuyoColor.EMPTY)
        player = make_diagnostics().request.public.own
        self.assertEqual(replace(player, score_carry=100).score_carry, 100)
        with self.assertRaises(ValueError):
            replace(player, score_carry=-1)

    def test_checkpoint_contract_does_not_silently_load_legacy(self):
        c.validate_checkpoint_contract(c.checkpoint_contract())
        legacy = {
            "policy_type": "v1_7_bootstrap_manager",
            "model_state_dict": {},
            "tactic_ids": list(c.TACTIC_IDS) + ["survive", "all_clear"],
        }
        with self.assertRaisesRegex(ValueError, "legacy"):
            c.validate_checkpoint_contract(legacy)
        for key in c.checkpoint_contract():
            metadata = c.checkpoint_contract()
            metadata.pop(key)
            with self.assertRaises(ValueError):
                c.validate_checkpoint_contract(metadata)
        metadata = c.checkpoint_contract()
        metadata["tactic_ids"] = metadata["tactic_ids"][::-1]
        with self.assertRaises(ValueError):
            c.validate_checkpoint_contract(metadata)

    def test_rule_rl_and_receipt_intervention_contract(self):
        diag = make_diagnostics()
        with self.assertRaises(ValueError):
            replace(diag.selection, value=0)
        with self.assertRaises(ValueError):
            replace(diag.selection, selector_kind="rl")
        rl = replace(
            diag.selection,
            selector_kind="rl",
            selector_checkpoint=c.semantic_digest("weights"),
            behavior_log_prob=-0.5,
            value=0.2,
        )
        self.assertEqual(rl.validate_batch(diag.batch), diag.batch.candidates[0])
        with self.assertRaises(ValueError):
            replace(rl, behavior_log_prob=0.1)
        for outcome in ("fallback", "stale", "timeout"):
            self.assertFalse(replace(diag.receipt, outcome=outcome).actor_trainable)
        with self.assertRaises(ValueError):
            replace(
                diag,
                receipt=replace(
                    diag.receipt,
                    pre_execution_snapshot_digest=c.semantic_digest("stale"),
                ),
            )


if __name__ == "__main__":
    unittest.main()
