"""Replay finite probes on archived public inputs, not whole-game A/B QA."""
import gzip
import json
from pathlib import Path
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices
from agents.nextgen_shared_search import ResponseBudget, _public_state
from agents.nextgen_survival import evidence_for, probe

ROOT = Path(__file__).resolve().parent


def compare():
    cohorts = []
    for path in sorted((ROOT.parent / 'desktop-survival-20260928/before').glob('*.json.gz')):
        if '.locks.' in path.name:
            continue
        raw = json.loads(gzip.decompress(path.read_bytes()))
        changes, selected_changes, ranking_changes, tested = [], [], [], 0
        for i, (wire, row) in enumerate(zip(raw['ledger'], raw['rows']), 1):
            request = c.NextgenRequest.from_dict(wire['request'])
            state, complete = _public_state(request)
            roots = legal_action_indices(state)
            with patch('agents.nextgen_survival.continuation_actions', legal_action_indices):
                before, bd = probe(request, state, roots, ResponseBudget(256), board_complete=complete)
            after, ad = probe(request, state, roots, ResponseBudget(256), board_complete=complete)
            assert bd['nodes'] <= 128 and ad['nodes'] <= 128
            tested += 1
            changed = [a for a in before if before[a] != after[a]]
            ranking_changed = [a for a in before if
                evidence_for(before[a], board_complete=complete) !=
                evidence_for(after[a], board_complete=complete)]
            if ranking_changed:
                ranking_changes.append({'decision': i, 'roots': ranking_changed})
            if changed:
                changes.append({'decision': i, 'roots': changed,
                                'before': bd, 'after': ad})
            action = row['action']
            if action in changed:
                selected_changes.append({'decision': i, 'action': action,
                    'before_status': before[action].status, 'after_status': after[action].status,
                    'before_witness': list(before[action].witness), 'after_witness': list(after[action].witness)})
        cohorts.append({'identity': path.name, 'decisions_tested': tested,
                        'ranking_evidence_changed': ranking_changes,
                        'changed_selected_root': selected_changes, 'changes': changes})
    return {'scope': 'frozen public request/probe regression only; not rerun policies, receipts or games',
            'quotas': {'response': 256, 'survival': 128}, 'cohorts': cohorts}


if __name__ == '__main__':
    value = compare()
    ROOT.joinpath('probe-comparison.json').write_text(json.dumps(value, indent=2) + '\n')
    for row in value['cohorts']:
        print(row['identity'], row['decisions_tested'], row['changed_selected_root'])
