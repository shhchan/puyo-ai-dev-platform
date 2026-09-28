"""Cell identity and public-only evidence regressions for the offline audit."""

import gzip
import json
import unittest
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState
from agents.nextgen_shared_search import _pairs, _public_state
from agents.template_catalog import load_template_catalog
from eval.nextgen_asset_diagnostic import audit_run, public_plan, trace_placement

ROOT = Path(__file__).resolve().parents[1]


def state(rows):
    planes = [0] * 6
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            if int(value):
                planes[int(value) - 1] |= 1 << (6 * y + x)
    return CompactSearchState(tuple(planes))


class AssetDiagnosticTests(unittest.TestCase):
    def test_same_color_replacement_does_not_preserve_original_cell(self):
        board = state(("110000", "100000", "100000", "200000", "100000"))
        result, remaining, consumed = trace_placement(
            board, (c.PUBLIC_CELL_TO_COLOR[3],) * 2, 20,
            {"bottom_red": (0, 1), "upper_red": (0, 4)},
        )
        self.assertEqual(result.chain_count, 1)
        self.assertEqual(remaining, {"upper_red": (0, 1)})
        self.assertEqual(consumed, {"bottom_red": 1})
        self.assertEqual(result.state.color_at(0, 1), board.color_at(0, 1))

    def test_row_fourteen_asset_does_not_fall(self):
        board = state(("000000",) * 13 + ("100000",))
        _, remaining, consumed = trace_placement(
            board, (c.PUBLIC_CELL_TO_COLOR[2],) * 2, 20, {"permanent": (0, 13)},
        )
        self.assertEqual(remaining, {"permanent": (0, 13)})
        self.assertEqual(consumed, {})

    def test_unused_template_is_descriptive_not_a_rejection(self):
        board = state(("221004", "112004", "122004"))
        assets = {f"{x}:{y}": (x, y) for y in range(3) for x in range(3)}
        value = public_plan(board, ((c.PUBLIC_CELL_TO_COLOR[4],) * 2,), (20,), assets)
        self.assertEqual(value["max_chain"], 1)
        self.assertEqual(value["used_count"], 0)
        self.assertEqual(value["remaining_count"], 9)
        self.assertFalse(value["game_over"])
        self.assertNotIn("penalty", value)
        with self.assertRaisesRegex(ValueError, "public"):
            public_plan(board, ((c.PUBLIC_CELL_TO_COLOR[4],) * 2,), (20, 20), assets)

    def test_saved_target_fire_consumes_all_eight_gtr_cells(self):
        path = ROOT / "docs/benchmarks/puyo-268-template-integration/public-prefix-compact-20260927/nextgen-55-40.json.gz"
        raw = json.loads(gzip.decompress(path.read_bytes()))
        catalog = load_template_catalog(ROOT / "train/config/nextgen_templates.yaml")
        audit = audit_run(raw, catalog)
        fire, = audit["fires"]
        self.assertEqual(fire["asset_status"], "fully_used")
        self.assertEqual(fire["selected"]["used_count"], 8)
        self.assertEqual(fire["selected"]["remaining_count"], 0)
        self.assertEqual(fire["selected"]["max_chain"], 10)
        self.assertEqual(audit["gaps"], [])
        self.assertFalse(fire["public_board_complete"])
        self.assertEqual(fire["alternative_status"], "public_estimate")
        raw["ledger"][0]["receipt"]["executed_action"] = 20
        with self.assertRaises(ValueError):
            audit_run(raw, catalog)

    def test_target_fire_may_retain_assets_without_becoming_invalid(self):
        path = ROOT / "docs/benchmarks/puyo-268-template-integration/public-prefix-compact-20260927/nextgen-55-40.json.gz"
        raw = json.loads(gzip.decompress(path.read_bytes()))
        request = c.NextgenRequest.from_dict(raw["ledger"][31]["request"])
        board, _ = _public_state(request)
        # This synthetic label set is NOT the GUI's unrecorded GTR run.
        assets = {f"{x}:{y}": (x, y) for y in range(12) for x in range(6)
                  if board.occupied_mask & (1 << (y * 6 + x))}
        _, survivors, _ = trace_placement(board, _pairs(request.public.own.known_pieces)[0], 8, assets)
        retained = {key: assets[key] for key in survivors}
        value = public_plan(board, _pairs(request.public.own.known_pieces), (8,), retained)
        self.assertEqual(value["max_chain"], 10)
        self.assertGreater(value["remaining_count"], 0)
        self.assertEqual(value["used_count"], 0)
        self.assertFalse(value["game_over"])


if __name__ == "__main__":
    unittest.main()
