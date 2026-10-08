"""A future plan with missing eligibility must not hide an eligible large fire."""
from dataclasses import replace
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from agents import nextgen_contracts as c
from agents.deep_chain_search_backend import PythonLongHorizonSearchBackend
from agents.long_horizon_search import LongHorizonSearchConfig
from agents.nextgen_shared_search import SharedSearchBatchBuilder, scenario_provenance
from agents.nextgen_survival import apply_envelope, value
from agents.nextgen_tactic_manager import RuleTacticSelector


class FireEligibilityTests(unittest.TestCase):
    def test_real_search_batch_and_selector_choose_existing_eligible_ten_chain(self):
        path = Path(__file__).resolve().parents[1] / (
            'docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/'
            'inference-v1-fixed/gtr-126.json.gz')
        raw = json.loads(gzip.decompress(path.read_bytes()))
        req = c.NextgenRequest.from_dict(raw['ledger'][32]['request'])
        cfg = LongHorizonSearchConfig(depth=3, width=4, scenarios=1,
                                      minimum_chain_count=10, max_expanded_nodes=2000, decision_seed=23)
        req = replace(req, control=replace(req.control,
            search_profile=replace(req.control.search_profile, shared_quota=2000, template_quota=0),
            scenario_provenance=scenario_provenance(req.public.own.known_pieces, cfg)))
        execution = SharedSearchBatchBuilder(PythonLongHorizonSearchBackend(), cfg).build(req)
        batch = execution.batch
        row = next(t for t in batch.tactics if t.tactic_id == 'fire_main')
        candidates = {v.candidate_id: v for v in batch.candidates}
        best = candidates[row.best_id]
        self.assertEqual((best.root_action, len(best.plan), value(best, 'chain_count'),
                          value(best, 'fatal_rate')), (12, 1, 10, 0))
        # Missing evidence remains missing and the future alternatives remain
        # available. The immutable tactic order owns the choice.
        future = [candidates[cid] for cid in row.candidate_ids if len(candidates[cid].plan) > 1]
        self.assertTrue(future)
        self.assertTrue(all(value(candidate, 'fatal_rate') is None for candidate in future))
        selected = RuleTacticSelector().select(req, batch, SimpleNamespace(action_mask=batch.action_mask),
                                               SimpleNamespace(threat='none'))
        selected = apply_envelope(batch, selected)
        self.assertEqual((selected.selected_tactic_id, selected.validate_batch(batch).root_action),
                         ('fire_main', 12))
        self.assertLessEqual(batch.counters.shared_nodes, 2000)
        self.assertLessEqual(batch.counters.response_nodes, 256)


if __name__ == '__main__':
    unittest.main()
