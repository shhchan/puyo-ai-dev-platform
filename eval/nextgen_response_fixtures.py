"""Fixed public threat candidate coverage; no win-rate or quality promotion claim."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from time import process_time

from agents import nextgen_contracts as c
from agents.deep_chain_search_backend import PythonLongHorizonSearchBackend
from agents.long_horizon_search import LongHorizonSearchConfig
from agents.nextgen_response_search import PublicResponseProvider
from agents.nextgen_shared_search import SharedSearchBatchBuilder, scenario_provenance
from puyo_env.nextgen_public_snapshot import TickInterval, TimingProfile
from src.core.realtime import RealtimeTimingConfig

CONFIG = LongHorizonSearchConfig(
    depth=3,
    width=2,
    scenarios=1,
    minimum_chain_count=2,
    max_expanded_nodes=1,
    decision_seed=250,
)
TIMING = TimingProfile(
    RealtimeTimingConfig(),
    0,
    21,
    30,
    70,
    "configured",
    0,
    120,
    TickInterval(1, 2, "public_estimate", "fixture_lock_cadence"),
)
EMPTY = ((0,) * 6,) * 14
FIRE = ((0,) * 6,) * 13 + ((1, 1, 1, 0, 0, 0),)
# Stable side tower exposes a red trigger beside the first five garbage rows.
COUNTER = (
    ((0,) * 6,) * 7
    + ((1, 0, 0, 0, 0, 0),) * 3
    + tuple((2 + i % 3, 0, 0, 0, 0, 0) for i in range(4))
)


def make_request(
    *,
    board=FIRE,
    pieces=((1, 2), (2, 3), (3, 4)),
    incoming=1,
    arrival=0,
    quota=2000,
    timing=TIMING,
    mask=None,
    carry=0,
    bonus=False,
):
    player = c.PublicPlayerState(
        board,
        pieces,
        "control",
        (c.PublicAttackPacket("fixture_packet", incoming, arrival, None),)
        if incoming
        else (),
        carry,
        bonus,
        False,
        False,
    )
    public = c.PublicSnapshot(player, replace(player, attack_packets=()), ())
    h = c.semantic_digest("response-fixtures.v1")
    return c.NextgenRequest(
        c.DecisionIdentity("fixture", 0, 1, "fixture", public.digest),
        public,
        timing.execution_context((True,) * c.NUM_ACTIONS if mask is None else mask, 0),
        c.ControlContext(
            c.PhaseSnapshot(None, None, False, 0, 14, 0, "unknown", None, 0),
            c.SearchProfile("response-fixture", 0, 0, quota),
            h,
            scenario_provenance(pieces, CONFIG),
        ),
    )


def build(request, *, timing=TIMING, horizon=3):
    return SharedSearchBatchBuilder(
        PythonLongHorizonSearchBackend(),
        CONFIG,
        response_provider=PublicResponseProvider(timing, horizon=horizon),
    ).build(request)


FIXTURES = (
    ("same_boundary_cancel_after_arrival", {"incoming": 1, "carry": 30}, "cancel"),
    ("insufficient_cancel", {"incoming": 61}, "cancel"),
    (
        "prepare_cancel_before_future_arrival",
        {"board": EMPTY, "pieces": ((1, 1), (1, 1)), "incoming": 31, "arrival": 10000},
        "cancel",
    ),
    (
        "counter_incoming31",
        {"board": COUNTER, "pieces": ((3, 4), (1, 1)), "incoming": 31},
        "counter",
    ),
    (
        "counter_incoming61",
        {"board": COUNTER, "pieces": ((3, 4), (1, 1)), "incoming": 61},
        "counter",
    ),
    (
        "counter_incoming120",
        {"board": COUNTER, "pieces": ((3, 4), (1, 1)), "incoming": 120},
        "counter",
    ),
    (
        "conditional_remainder_columns",
        {"board": COUNTER, "pieces": ((3, 4), (1, 1)), "incoming": 29, "quota": 4000},
        "counter",
    ),
    ("public_short_attack_without_threat", {"incoming": 0}, "decisive_short_attack"),
)


def report():
    rows = []
    for name, values, tactic in FIXTURES:
        req = make_request(**values)
        start = process_time()
        result = build(req)
        elapsed = process_time() - start
        summary = result.batch.tactics[c.TACTIC_IDS.index(tactic)]
        rows.append(
            {
                "fixture": name,
                "expected_tactic": tactic,
                "covered": summary.available,
                "candidate_count": len(summary.candidate_ids),
                "response_nodes": result.batch.counters.response_nodes,
                "response_quota": req.control.search_profile.response_quota,
                "feature_evaluations": result.batch.counters.feature_evaluations,
                "elapsed_ms": result.batch.counters.elapsed_ms,
                "cpu_ms": elapsed * 1000,
                "status": result.response_result.status,
                "cutoff_reason": result.response_result.cutoff_reason,
            }
        )
    return {
        "schema": "puyo.nextgen.response_fixture_report.v1",
        "candidate_gap": sum(not r["covered"] for r in rows),
        "fixtures": rows,
        "limitation": "Public conditional witnesses only; no measured latency or G2 promotion.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = report()
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(payload, end="")
    raise SystemExit(bool(result["candidate_gap"]))
