"""Selector separation and actual scheduler receipt/ledger integration."""

import copy
import json
import unittest
from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.nextgen_shared_search import (
    PreparedTemplateSearch,
    SharedSearchBatchBuilder,
)
from agents.nextgen_tactic_manager import (
    NextgenTacticManagerPolicy,
    RuleSelectorConfig,
    RuleTacticSelector,
)
from puyo_env.realtime_ai import (
    PolicyProcessExecutor,
    RealtimeDecisionConfig,
    RealtimePolicyController,
)
from puyo_env.realtime_versus import RealtimeVersusMatch
from selfplay.policies import make_policy
from src.core.constants import PuyoColor
from src.core.puyo import Puyo
from tests.test_nextgen_shared_search import catalog, config, request


def policy():
    return NextgenTacticManagerPolicy(
        catalog=catalog(),
        search_config=config(),
        profile=c.SearchProfile("test", 80, 100, 20),
        seed=19,
    )


def all_tactics(board=None):
    p = policy()
    req = request(
        p.search_config,
        quota=(80, 100, 20),
        active=True,
        threat=True,
        cat=p.catalog,
        board=board,
    )
    execution = SharedSearchBatchBuilder(
        p.backend, p.search_config, template_catalog=p.catalog
    ).build(req)
    candidate = replace(
        execution.batch.candidates[0], tactics=c.TACTIC_IDS, fallback=False, rank=0
    )
    rows = tuple(
        c.TacticSummary(
            t,
            (candidate.candidate_id,),
            candidate.candidate_id,
            True,
            "available",
            "partial",
            False,
            (),
        )
        for t in c.TACTIC_IDS
    )
    batch = replace(execution.batch, candidates=(candidate,), tactics=rows)
    return req, batch


def with_evidence(batch, tactic, **values):
    return replace(
        batch,
        tactics=tuple(
            replace(
                row,
                evidence=tuple(
                    c.NamedEvidence(
                        n, c.NumericEvidence(v, "partial", "public_estimate")
                    )
                    for n, v in values.items()
                ),
            )
            if row.tactic_id == tactic
            else row
            for row in batch.tactics
        ),
    )


class SelectorTests(unittest.TestCase):
    def select(self, req, batch, threat="none", selector=None):
        return (selector or RuleTacticSelector()).select(
            req,
            batch,
            c.build_features({}, batch.action_mask),
            SimpleNamespace(threat=threat),
        )

    def test_pressing_does_not_disable_template_or_change_mask(self):
        req, batch = all_tactics()
        before = batch.to_dict()
        selection = self.select(req, batch, "pressing")
        self.assertEqual(selection.selected_tactic_id, "build_template")
        self.assertEqual(batch.to_dict(), before)

    def test_six_configured_preferences_and_short_judgment_are_not_masks(self):
        req, batch = all_tactics()
        self.assertTrue(batch.action_mask[5])
        self.assertEqual(self.select(req, batch).selected_tactic_id, "build_template")
        with self.assertRaises(ValueError):
            RuleSelectorConfig(priority=(*c.TACTIC_IDS, "fallback"))
        selector = RuleTacticSelector(
            RuleSelectorConfig(priority=("build_main", *c.TACTIC_IDS[1:]))
        )
        self.assertEqual(
            self.select(req, batch, selector=selector).selected_tactic_id, "build_main"
        )

    def test_immediate_cancel_counter_and_insufficient_response_comparison(self):
        req, batch = all_tactics()
        batch = with_evidence(
            batch, "cancel", canceled=12, fire_end_upper=10, deadline_lower=30
        )
        self.assertEqual(
            self.select(req, batch, "immediate").selected_tactic_id, "cancel"
        )
        batch = with_evidence(batch, "cancel", canceled=1, fatal_rate=1)
        batch = with_evidence(batch, "counter", canceled=3, fatal_rate=0)
        self.assertEqual(
            self.select(req, batch, "immediate").selected_tactic_id, "counter"
        )
        batch = with_evidence(batch, "cancel", canceled=4, fatal_rate=0)
        self.assertEqual(
            self.select(req, batch, "immediate").selected_tactic_id, "cancel"
        )
        self.assertTrue(batch.action_mask[3])

    def test_short_attack_saturated_main_and_build_main(self):
        req, batch = all_tactics()
        # Public occupancy only changes the teacher's preference.
        req = replace(
            req,
            control=replace(
                req.control,
                phase=replace(req.control.phase, active=False, remaining_decisions=0),
            ),
        )
        batch = with_evidence(batch, "fire_main", chain_count=6, fatal_rate=0)
        self.assertEqual(self.select(req, batch).selected_tactic_id, "fire_main")
        batch = with_evidence(batch, "fire_main", chain_count=5, fatal_rate=0)
        self.assertEqual(self.select(req, batch).selected_tactic_id, "build_main")
        batch = with_evidence(batch, "decisive_short_attack", outgoing=40)
        selector = RuleTacticSelector(RuleSelectorConfig(opponent_occupied_cells=1))
        # Public source was empty: no positive teacher judgment, still unmasked.
        self.assertEqual(
            self.select(req, batch, selector=selector).selected_tactic_id, "build_main"
        )

    def test_short_attack_teacher_positive_estimate_is_separate_from_mask(self):
        req, batch = all_tactics(board=((0,) * 6,) * 13 + ((1, 0, 0, 0, 0, 0),))
        batch = with_evidence(batch, "decisive_short_attack", outgoing=40)
        selector = RuleTacticSelector(RuleSelectorConfig(opponent_occupied_cells=1))
        self.assertEqual(
            self.select(req, batch, selector=selector).selected_tactic_id,
            "decisive_short_attack",
        )
        self.assertEqual(batch.action_mask, (True,) * 6)

    def test_same_batch_can_be_selected_by_future_rl_without_search(self):
        req, batch = all_tactics()
        with patch(
            "agents.nextgen_shared_search.SharedSearchBatchBuilder.build",
            side_effect=AssertionError("second search"),
        ):
            selection = self.select(req, batch)
            rl = replace(
                selection,
                selector_kind="rl",
                selector_checkpoint="a" * 64,
                behavior_log_prob=-0.5,
                value=0.0,
            )
            self.assertEqual(selection.validate_batch(batch), rl.validate_batch(batch))
            with self.assertRaises(ValueError):
                replace(selection, batch_digest="b" * 64).validate_batch(batch)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.match = RealtimeVersusMatch(seed=7)
        self.policy = policy()
        self.controller = RealtimePolicyController(self.policy)

    def test_factory_and_public_worker_boundary(self):
        self.assertIsInstance(
            make_policy("nextgen_tactic_manager", seed=1), NextgenTacticManagerPolicy
        )
        runtime = self.controller.nextgen_scheduler
        obs, info = runtime.prepare(self.match, "player_0", self.controller.config)
        self.assertEqual(obs, {})
        self.assertEqual(set(info), {"nextgen", "action_mask", "action_mask_source"})
        self.assertNotIn("simulator", info)
        self.assertEqual(info["nextgen"]["public"].own.visible_board[0], (None,) * 6)
        with self.assertRaises(KeyError):
            self.policy.select_action({}, {"simulator": object()})

    def test_activation_diagnostics_replay_and_ledger_have_identical_receipt(self):
        self.controller.next_input(self.match, "player_0")
        record = self.controller.diagnostics.last_decision
        runtime = self.controller.nextgen_scheduler
        self.assertEqual(runtime.errors, [])
        self.assertEqual(record.outcome, "activated")
        diagnostic = c.Diagnostics.from_dict(record.nextgen_diagnostics)
        self.assertEqual(record.requested_action, diagnostic.receipt.requested_action)
        self.assertEqual(record.executed_action, diagnostic.receipt.executed_action)
        self.assertEqual(
            json.loads(json.dumps(record.to_json()))["nextgen_diagnostics"],
            diagnostic.to_dict(),
        )
        self.assertEqual(runtime.ledger, [diagnostic])
        self.assertEqual(
            self.controller.latest_policy_diagnostics["nextgen"], diagnostic.to_dict()
        )
        row = runtime.decision_record(
            0,
            reward_components={"survival": 0.0},
            elapsed_match_ticks=1,
            next_decision_id=None,
            truncated=True,
        )
        self.assertEqual(row.diagnostics, diagnostic)
        self.assertEqual(
            self.policy.last_context.trace.steps[0].step_id, "template_phase"
        )

    def test_live_receipt_writes_and_loads_with_trajectory_schema(self):
        import tempfile

        from tests.test_nextgen_trajectory import provenance
        from train.nextgen_trajectory import (
            EpisodeRecord,
            iter_actor_samples,
            validate_nextgen_run,
            write_nextgen_run,
        )

        self.controller.next_input(self.match, "player_0")
        runtime = self.controller.nextgen_scheduler
        d = runtime.ledger[0]
        row = runtime.decision_record(
            0,
            reward_components={"survival": 0.0},
            elapsed_match_ticks=1,
            next_decision_id=None,
            truncated=True,
        )
        episode = EpisodeRecord(
            d.request.identity.episode_id,
            [row],
            {"status": "truncated", "winner": None, "end_reason": "test_boundary"},
            public_timing_history=self.match.public_timing_history(),
            actual_metrics={
                "chains": 0,
                "attack": 0,
                "canceled": 0,
                "received": 0,
                "survival_ticks": self.match.tick,
            },
        )
        source = provenance()
        source["search_profile"] = d.request.control.search_profile.to_dict()
        source["timing"] = {
            "schema_version": d.request.execution.timing_schema,
            "digest": d.request.execution.timing_digest,
            "latency_mode": d.request.execution.latency_mode,
        }
        source["template_schema"]["digest"] = d.request.control.template_config_hash
        source["dataset_split"]["train"] = [episode.episode_id]
        with tempfile.TemporaryDirectory() as directory:
            manifest = write_nextgen_run(
                run_dir=directory,
                run_id="live-test",
                episodes=[episode],
                config={"profile": "test"},
                provenance=source,
                git_commit="abc123",
            )
            self.assertEqual(validate_nextgen_run(directory), manifest)
            samples = list(iter_actor_samples(directory))
            self.assertEqual(
                samples, [(d.features.to_dict(), d.selection.selected_tactic_id)]
            )

    def test_match_once_and_reject_prepared_results_from_other_request_or_phase(self):
        from agents.nextgen_tactic_manager import match_templates

        with (
            patch(
                "agents.nextgen_tactic_manager.match_templates", wraps=match_templates
            ) as matcher,
            patch(
                "agents.nextgen_shared_search.match_templates",
                side_effect=AssertionError("double template quota"),
            ),
        ):
            self.controller.next_input(self.match, "player_0")
        self.assertEqual(matcher.call_count, 1)
        context = self.policy.last_context
        prepared = context.require("prepared_template")
        req = context.require("request")
        key = context.require("template_key")
        prepared.validate(req, self.policy.catalog, key)
        for changed in (
            replace(req, identity=replace(req.identity, request_id="other")),
            replace(
                req,
                control=replace(
                    req.control, phase=replace(req.control.phase, progress=0.5)
                ),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "request/phase"):
                prepared.validate(changed, self.policy.catalog, key)
        with self.assertRaisesRegex(ValueError, "catalog"):
            prepared.validate(
                req, replace(self.policy.catalog, semantic_digest="c" * 64), key
            )
        with self.assertRaisesRegex(ValueError, "quota"):
            PreparedTemplateSearch(
                prepared.request_digest,
                key,
                replace(prepared.result, coverage_nodes=101),
            ).validate(req, self.policy.catalog, key)

    def test_configured_timeout_keeps_requested_action_and_does_not_consume_phase(self):
        self.controller = RealtimePolicyController(
            self.policy,
            config=RealtimeDecisionConfig(inference_latency_ticks=2, timeout_ticks=0),
        )
        self.controller.next_input(self.match, "player_0")
        runtime = self.controller.nextgen_scheduler
        record = self.controller.diagnostics.last_decision
        self.assertEqual(record.outcome, "timeout")
        self.assertTrue(record.fallback)
        self.assertIsNotNone(record.requested_action)
        self.assertFalse(runtime.ledger[0].receipt.actor_trainable)
        self.assertEqual(runtime.phase.diagnostics()["consumed_decisions"], 0)
        self.assertTrue(runtime.phase._initialized)
        self.assertEqual(runtime.phase.diagnostics()["request_count"], 1)

    def test_snapshot_changes_during_latency_are_rejected_at_activation(self):
        self.controller = RealtimePolicyController(
            self.policy, config=RealtimeDecisionConfig(inference_latency_ticks=1)
        )
        self.controller.next_input(self.match, "player_0")
        self.match.schedule_attack("player_1", 1, delay_ticks=10)
        self.match.step({})
        self.controller.next_input(self.match, "player_0")
        record = self.controller.diagnostics.last_decision
        self.assertEqual(record.outcome, "stale")
        self.assertTrue(record.fallback)
        self.assertEqual(record.nextgen_diagnostics["receipt"]["outcome"], "stale")
        self.assertEqual(
            self.controller.nextgen_scheduler.phase.diagnostics()["consumed_decisions"],
            0,
        )

    def test_hidden_board_change_rechecks_authoritative_root(self):
        self.controller = RealtimePolicyController(
            self.policy, config=RealtimeDecisionConfig(inference_latency_ticks=1)
        )
        self.controller.next_input(self.match, "player_0")
        requested = self.controller.diagnostics.last_decision.requested_action
        # Public snapshot remains equal; authoritative reachable roots change.
        self.match.step({})
        real = __import__(
            "puyo_env.realtime_ai", fromlist=["realtime_reachable_action_mask"]
        ).realtime_reachable_action_mask

        def mask(simulator, **kwargs):
            value = real(simulator, **kwargs)
            value[requested] = False
            return value

        with patch(
            "puyo_env.realtime_ai.realtime_reachable_action_mask", side_effect=mask
        ):
            self.controller.next_input(self.match, "player_0")
        record = self.controller.diagnostics.last_decision
        self.assertEqual(record.outcome, "fallback")
        self.assertEqual(record.requested_action, requested)
        self.assertNotEqual(record.executed_action, requested)
        self.assertEqual(record.reason, "activation_unreachable_fallback")

    def test_actual_hidden_row_legality_is_rechecked_even_when_public_digest_matches(
        self,
    ):
        self.policy.profile = c.SearchProfile("fallback_only", 0, 0, 0)
        self.controller = RealtimePolicyController(
            self.policy, config=RealtimeDecisionConfig(inference_latency_ticks=1)
        )
        self.controller.next_input(self.match, "player_0")
        self.assertEqual(self.controller.diagnostics.last_decision.requested_action, 0)
        before = self.match.public_snapshot().digest
        self.match.player_states["player_0"].simulator.game.field.place_puyo(
            0, 13, Puyo(PuyoColor.BLUE)
        )
        self.assertEqual(self.match.public_snapshot().digest, before)
        self.match.step({})
        self.controller.next_input(self.match, "player_0")
        record = self.controller.diagnostics.last_decision
        self.assertEqual(record.outcome, "fallback")
        self.assertNotEqual(record.executed_action, record.requested_action)
        self.assertFalse(
            self.controller.nextgen_scheduler.ledger[0].receipt.actor_trainable
        )

    def test_replan_same_pair_consumes_at_most_once(self):
        self.controller.next_input(self.match, "player_0")
        phase = self.controller.nextgen_scheduler.phase
        self.assertEqual(phase.phase.consumed_decisions, 1)
        phase_id = phase.phase.phase_id
        self.controller._active_plan = None
        self.controller.next_input(self.match, "player_0")
        phase = self.controller.nextgen_scheduler.phase
        self.assertEqual(phase.phase.phase_id, phase_id)
        self.assertEqual(phase.phase.consumed_decisions, 1)
        self.assertEqual(len(self.controller.nextgen_scheduler.ledger), 2)

    def test_counter_witness_replans_after_actual_first_drop(self):
        from eval.nextgen_response_fixtures import COUNTER

        game = self.match.player_states["player_0"].simulator.game
        for y, row in enumerate(reversed(COUNTER)):
            for x, cell in enumerate(row):
                if cell:
                    game.field.place_puyo(x, y, Puyo(c.PUBLIC_CELL_TO_COLOR[cell]))
        game.current_puyo_1, game.current_puyo_2 = (
            Puyo(PuyoColor.GREEN),
            Puyo(PuyoColor.YELLOW),
        )
        game.next_puyo_queue[0] = (Puyo(PuyoColor.RED), Puyo(PuyoColor.RED))
        self.policy.profile = c.SearchProfile("counter", 0, 0, 2000)
        self.match.schedule_attack("player_1", 31, delay_ticks=0)
        first_input = self.controller.next_input(self.match, "player_0")
        first = self.controller.nextgen_scheduler.ledger[0]
        self.assertEqual(first.selection.selected_tactic_id, "counter")
        candidate = first.selection.validate_batch(first.batch)
        self.assertEqual(len(candidate.plan), 2)
        self.match.step({"player_0": first_input})
        # Complete only the current pair. The next controlled board must issue
        # another policy request and bind the observed garbage result.
        for _ in range(200):
            value = self.controller.next_input(self.match, "player_0")
            self.match.step({"player_0": value})
            if len(self.controller.nextgen_scheduler.ledger) >= 2:
                break
        self.assertEqual(len(self.controller.nextgen_scheduler.ledger), 2)
        second = self.controller.nextgen_scheduler.ledger[1]
        self.assertNotEqual(
            first.request.identity.request_id, second.request.identity.request_id
        )
        self.assertNotEqual(first.request.public.digest, second.request.public.digest)
        self.assertTrue(
            any(e.kind == "drop" for e in self.match.public_timing_history().packets)
        )
        self.assertEqual(second.request.public.own.known_pieces[0], (1, 1))

    def test_no_root_and_terminal_board_do_not_call_policy(self):
        with (
            patch.object(
                self.policy,
                "select_action",
                side_effect=AssertionError("policy called"),
            ),
            patch(
                "puyo_env.realtime_ai.realtime_reachable_action_mask",
                return_value=[False] * 22,
            ),
        ):
            self.controller.next_input(self.match, "player_0")
        self.assertEqual(self.controller.diagnostics.last_event, "no_reachable_root")
        self.match.player_states["player_0"].simulator.game.game_over = True
        with patch.object(
            self.policy, "select_action", side_effect=AssertionError("policy called")
        ):
            self.controller.next_input(self.match, "player_0")

    def test_private_future_changes_do_not_change_candidate_features_or_selection(self):
        runtime = self.controller.nextgen_scheduler
        obs, info = runtime.prepare(self.match, "player_0", self.controller.config)
        first = self.policy.select_action(obs, info)
        before = copy.deepcopy(self.policy.tactical_diagnostics["nextgen"])
        game = self.match.player_states["player_0"].simulator.game
        game.next_puyo_queue.append((Puyo(PuyoColor.PURPLE), Puyo(PuyoColor.PURPLE)))
        game.field.place_puyo(0, 13, Puyo(PuyoColor.BLUE))
        self.assertEqual(
            self.match.public_snapshot().digest, info["nextgen"]["public"].digest
        )
        self.assertEqual(self.policy.select_action(obs, info), first)
        after = self.policy.tactical_diagnostics["nextgen"]
        self.assertEqual(before["features"], after["features"])
        self.assertEqual(before["selection"], after["selection"])
        # Counters contain elapsed time; semantic digest explicitly excludes it.
        self.assertEqual(
            c.CandidateBatch.from_dict(before["batch"]).digest,
            c.CandidateBatch.from_dict(after["batch"]).digest,
        )

    def test_invalid_selection_falls_back_without_training_row_or_phase_consumption(
        self,
    ):
        original = self.policy.selector.select

        def wrong(*args):
            return replace(original(*args), batch_digest="f" * 64)

        with patch.object(self.policy.selector, "select", side_effect=wrong):
            self.controller.next_input(self.match, "player_0")
        self.assertTrue(self.controller.diagnostics.last_decision.fallback)
        self.assertTrue(self.controller.nextgen_scheduler.errors)
        self.assertEqual(self.controller.nextgen_scheduler.ledger, [])

    def test_spawned_process_uses_same_phase_owner_and_returns_wire_diagnostics(self):
        executor = PolicyProcessExecutor(self.policy)
        try:
            runtime = self.controller.nextgen_scheduler
            obs, info = runtime.prepare(self.match, "player_0", self.controller.config)
            selected, elapsed, payload = executor.submit_policy(obs, info).result(
                timeout=15
            )
            self.assertIsNone(runtime.accept(payload, selected))
            self.assertGreaterEqual(elapsed, 0)
            self.assertEqual(
                payload["nextgen"]["request"]["identity"],
                info["nextgen"]["identity"].to_dict(),
            )
            self.assertEqual(runtime.phase.diagnostics()["consumed_decisions"], 0)
            self.assertIsNone(self.policy.last_context)
        finally:
            executor.shutdown(wait=True)

    def test_process_controller_emits_measured_receipt(self):
        executor = PolicyProcessExecutor(self.policy)
        try:
            controller = RealtimePolicyController(
                self.policy,
                decision_executor=executor,
                config=RealtimeDecisionConfig(
                    latency_mode="measured", timeout_ticks=120
                ),
            )
            controller.next_input(self.match, "player_0")
            controller._async_decision.future.result(timeout=15)
            self.match.step({})
            controller.next_input(self.match, "player_0")
            record = controller.diagnostics.last_decision
            self.assertEqual(record.outcome, "activated")
            self.assertEqual(record.latency_mode, "measured")
            self.assertEqual(record.completion_tick, self.match.tick)
            self.assertEqual(record.activation_tick, self.match.tick)
            self.assertEqual(
                record.nextgen_diagnostics,
                controller.nextgen_scheduler.ledger[0].to_dict(),
            )
        finally:
            executor.shutdown(wait=True)

    def test_async_timeout_without_selection_is_an_attempt_not_invented_ledger_data(
        self,
    ):
        class Executor:
            def submit(inner, function):
                return Future()

        controller = RealtimePolicyController(
            self.policy,
            decision_executor=Executor(),
            config=RealtimeDecisionConfig(latency_mode="measured", timeout_ticks=0),
        )
        controller.next_input(self.match, "player_0")
        record = controller.diagnostics.last_decision
        self.assertEqual(record.outcome, "timeout")
        self.assertIsNone(record.requested_action)
        self.assertIsNotNone(record.executed_action)
        self.assertIsNone(record.nextgen_diagnostics)
        self.assertEqual(controller.nextgen_scheduler.ledger, [])
        self.assertTrue(controller.nextgen_scheduler.errors)

    def test_async_stale_piece_preserves_requested_action_in_receipt(self):
        class Executor:
            def submit(inner, function):
                inner.future = Future()
                inner.function = function
                return inner.future

        executor = Executor()
        self.controller = RealtimePolicyController(
            self.policy, decision_executor=executor
        )
        self.controller.next_input(self.match, "player_0")
        result = executor.function()
        game = self.match.player_states["player_0"].simulator.game
        game.current_puyo_1 = Puyo(game.current_puyo_1.color)
        executor.future.set_result(result)
        self.controller.next_input(self.match, "player_0")
        record = self.controller.diagnostics.last_decision
        self.assertEqual(record.outcome, "stale")
        self.assertEqual(
            record.nextgen_diagnostics["receipt"]["requested_action"], result[0]
        )
        self.assertIsNone(record.nextgen_diagnostics["receipt"]["executed_action"])


if __name__ == "__main__":
    unittest.main()
