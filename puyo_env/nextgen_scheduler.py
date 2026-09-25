"""Authoritative scheduler bridge for public-only nextgen workers."""

from __future__ import annotations

import copy
from dataclasses import replace

from agents import nextgen_contracts as c
from agents.nextgen_tactic_manager import match_result_from_dict, reconcile_phase
from agents.template_phase import TemplatePhaseController
from puyo_env.nextgen_public_snapshot import TickInterval, TimingProfile


class NextgenScheduler:
    def __init__(self, policy):
        self.policy = policy
        self.reset()

    def reset(self):
        self.phase = TemplatePhaseController(self.policy.catalog, seed=self.policy.template_seed)
        self.sequence = 0
        self.episode_index = getattr(self, "episode_index", 0) + 1
        self.episode_id = f"nextgen-episode-{self.episode_index}"
        self.data = None
        self.result = None
        self.result_phase = None
        self.ledger = []
        self.ledger_metadata = []
        self.errors = []
        self.last_payload = {}

    def prepare(self, match, agent, config):
        from puyo_env.realtime_ai import nextgen_authoritative_action_mask

        player = int(agent.rsplit("_", 1)[1])
        public = match.public_snapshot(player)
        history = match.public_timing_history()
        self.phase.observe(public, history, player_id=player, controllable=False)
        mask = nextgen_authoritative_action_mask(
            match.player_states[agent].simulator,
            timing=match.timing,
            max_expanded_states=config.max_plan_expanded_states,
        )
        if not any(mask):
            return None
        self.sequence += 1
        identity = c.DecisionIdentity(
            self.episode_id,
            player,
            self.sequence,
            f"request-{self.sequence}",
            public.digest,
        )
        # Explicit smoke estimate, never promoted to a lock/landing guarantee.
        timing = TimingProfile.from_match(
            match,
            latency_mode=config.latency_mode,
            inference_latency_ticks=config.inference_latency_ticks,
            timeout_ticks=config.timeout_ticks
            if config.timeout_ticks is not None
            else 2**31 - 1,
            operation_cadence=TickInterval(
                1, 120, "public_estimate", "smoke_operation_estimate"
            ),
        )
        self.data = {
            "identity": identity,
            "public": public,
            "history": history,
            "timing": timing,
            "execution": timing.execution_context(mask, match.tick),
            "phase": copy.deepcopy(self.phase),
            "piece_id": f"piece-{sum(e.kind == 'placement' and e.player_id == player for e in public.events)}",
        }
        self.result = None
        self.result_phase = None
        self.last_payload = {}
        # No simulator, queue, RNG, observation board or privileged runtime info
        # crosses this boundary, including when using the spawned executor.
        return {}, {
            "nextgen": self.data,
            "action_mask": mask,
            "action_mask_source": "reachable_planner",
        }

    def accept(self, payload, selected_action):
        try:
            diagnostics = c.Diagnostics.from_dict(payload["nextgen"])
            request = diagnostics.request
            if (
                request.identity != self.data["identity"]
                or request.public != self.data["public"]
                or request.execution != self.data["execution"]
                or request.control.search_profile != self.policy.profile
                or request.control.template_config_hash
                != self.policy.catalog.semantic_digest
            ):
                raise ValueError("worker request mismatch")
            candidate = diagnostics.selection.validate_batch(diagnostics.batch)
            if candidate.root_action != selected_action:
                raise ValueError("worker action/selection mismatch")
            phase = copy.deepcopy(self.phase)
            reconcile_phase(
                phase,
                match_result_from_dict(payload["template_match"]),
                request.public,
                self.data["history"],
                request.identity,
            )
            if phase.phase_snapshot() != request.control.phase:
                raise ValueError("worker phase mismatch")
            self.result, self.result_phase = diagnostics, phase
            # Initial selection/reconciliation is control state, not a tactic
            # activation. Keep it across timeout/retry; consumption is below.
            self.phase = phase
            self.last_payload = copy.deepcopy(payload)
            return None
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            reason = f"nextgen_policy_error: {exc}"
            self.errors.append(
                {"request_id": self.data["identity"].request_id, "reason": reason}
            )
            return reason

    def stale(self, match):
        return (
            match.public_snapshot(self.data["identity"].player_id).digest
            != self.data["public"].digest
        )

    def finish(self, match, record, *, outcome=None):
        outcome = outcome or (
            "timeout"
            if record.timeout
            else "fallback"
            if record.fallback
            else "activated"
        )
        if self.result is None:
            return replace(record, outcome=outcome)
        result = self.result
        candidate = result.selection.validate_batch(result.batch)
        player = result.request.identity.player_id
        public, history = match.public_snapshot(player), match.public_timing_history()
        reason = record.reason
        if result.selection.reason in ("legitimate_survival_exception", "survival_safe_nonfire"):
            adoption = "survival_adopted" if outcome == "activated" else "survival_not_adopted_" + outcome
            reason = (adoption + ":" + result.selection.reason + ":" + reason)[:256]
        receipt = c.ExecutionReceipt(
            candidate.candidate_id,
            candidate.root_action,
            record.action_index if record.activation_tick is not None else None,
            outcome,
            result.request.execution.request_tick,
            record.completion_tick,
            record.activation_tick,
            result.request.execution.timeout_tick,
            public.digest,
            reason,
        )
        diagnostics = replace(result, receipt=receipt)
        self.phase = self.result_phase
        self.phase.activate(
            piece_id=self.data["piece_id"],
            request_id=result.request.identity.request_id,
            tactic=result.selection.selected_tactic_id,
            adopted=outcome == "activated",
            snapshot=public,
            history=history,
            player_id=player,
            target_packet_ids=tuple(p.packet_id for p in public.own.attack_packets)
            if result.selection.selected_tactic_id in ("cancel", "counter")
            else (),
        )
        self.ledger.append(diagnostics)
        template_selection = self.phase.selection
        self.ledger_metadata.append(
            {
                "phase_after": self.phase.phase_snapshot().to_dict(),
                "switch_reason": self.phase.exit_reason,
                "template_score": template_selection.candidate.score
                if template_selection and template_selection.candidate
                else None,
                "template_probability": template_selection.probability
                if template_selection
                else None,
                "template_rng_position": template_selection.rng_position
                if template_selection
                else None,
            }
        )
        self.last_payload["nextgen"] = diagnostics.to_dict()
        self.last_payload["template_phase"] = self.phase.diagnostics()
        return replace(
            record,
            nextgen_diagnostics=diagnostics.to_dict(),
            requested_action=candidate.root_action,
            executed_action=receipt.executed_action,
            outcome=outcome,
        )

    def decision_record(
        self,
        index,
        *,
        reward_components,
        elapsed_match_ticks,
        next_decision_id,
        **kwargs,
    ):
        """Bind caller-observed rewards/terminal data to the exact emitted receipt."""
        from train.nextgen_trajectory import DecisionRecord

        return DecisionRecord(
            self.ledger[index],
            reward_components,
            elapsed_match_ticks,
            next_decision_id,
            **{**self.ledger_metadata[index], **kwargs},
        )
