"""Frozen public fixture audit; no native search or private runtime input."""
import argparse
import json
from pathlib import Path
from tests.test_nextgen_inferred_survival import fixtures, execute, selection

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
rows = []
for case, (request, batch, selected, order) in fixtures().items():
    roots, diagnostics, budget, _ = execute(request, order)
    action, reason = selection(batch, selected, roots, diagnostics['active'])
    rows.append({'case': case, 'nodes': budget.nodes, 'selected_root': action,
                 'selection_reason': reason, 'diagnostics': diagnostics})
result = {
    'scope': 'Public observer deductions and frozen candidate/tactic order; actual apply_envelope, not a newly executed policy or game. Offline full boards are diagnostic-only and never runtime inputs.',
    'quotas': {'survival': 128, 'response': 256}, 'rows': rows,
}
args.output.write_text(json.dumps(result, indent=2) + '\n')
for row in rows:
    print(row['case'], row['nodes'], row['selected_root'], row['diagnostics']['control_proof']['status'])
