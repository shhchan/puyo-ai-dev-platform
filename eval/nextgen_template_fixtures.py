"""Replay fixed public-piece template fixtures through the legal game engine."""

from __future__ import annotations

import json
from pathlib import Path

from agents.nextgen_contracts import PUBLIC_CELL_TO_COLOR
from agents.template_catalog import TemplateSelector, load_template_catalog, match_templates
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.game import GameState
from src.core.puyo import Puyo

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "train/config/nextgen_templates.yaml"
CASES = ROOT / "tests/fixtures/nextgen_template_catalog_cases.json"
COLOR_TO_WIRE = {color: i for i, color in enumerate(PUBLIC_CELL_TO_COLOR)}


def _game(rows_bottom_up: list[str]) -> GameState:
    game = GameState(seed=0)
    assert len(rows_bottom_up) <= 12
    for y, row in enumerate(rows_bottom_up):
        assert len(row) == 6
        for x, char in enumerate(row):
            game.field.grid[y][x] = Puyo(PUBLIC_CELL_TO_COLOR[int(char)])
    return game


def _public_board(game: GameState) -> list[list[int | None]]:
    # Public visible rows only. Hidden rows stay unknown, even in this replay.
    return [[None] * 6 for _ in range(2)] + [
        [COLOR_TO_WIRE[puyo.color] for puyo in row]
        for row in reversed(game.field.grid[:12])
    ]


def _complete_for(catalog, board, key) -> bool:
    result = match_templates(catalog, board, (), node_budget=0, binding_budget=0)
    return any(candidate.key == key and candidate.complete for candidate in result.candidates)


def _any_complete_for(catalog, board, template_id, variant_id) -> bool:
    result = match_templates(catalog, board, (), node_budget=0, binding_budget=0)
    return any(
        candidate.template_id == template_id
        and candidate.variant_id == variant_id
        and candidate.complete
        for candidate in result.candidates
    )


def run_cases() -> dict:
    catalog = load_template_catalog(CATALOG)
    fixture = json.loads(CASES.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == "puyo.template_catalog_cases.v1"
    output = []
    for case in fixture["cases"]:
        template = next(t for t in catalog.templates if t.id == case["template_id"])
        assert template.enabled and template.commit_turns <= 14
        assert len(case["public_pieces"]) <= 14
        game = _game(case["start_rows_bottom_up"])
        chosen_key = None
        actions = []
        statuses = []
        for piece in case["public_pieces"]:
            game.current_puyo_1 = Puyo(PUBLIC_CELL_TO_COLOR[piece[0]])
            game.current_puyo_2 = Puyo(PUBLIC_CELL_TO_COLOR[piece[1]])
            game.state = "control"
            legal = tuple(
                game.find_landing_y(action.axis_x, action.rotation) is not None
                for action in PLACEMENT_ACTIONS
            )
            result = match_templates(
                catalog,
                _public_board(game),
                [piece],
                node_budget=fixture["node_budget"],
                binding_budget=fixture["binding_budget"],
                reachable_mask=legal,
            )
            if chosen_key is None:
                selection = TemplateSelector(catalog, fixture["selector_seed"]).select_initial(result)
                assert selection.candidate is not None
                chosen_key = selection.candidate.key
                assert chosen_key[:2] == (case["template_id"], case["variant_id"])
            candidate = next(c for c in result.candidates if c.key == chosen_key)
            statuses.append(candidate.fit_status)
            if candidate.fit_status != "fit" or not candidate.witness_actions:
                break
            assert candidate.known_prefix_length == 1
            action_index = candidate.witness_actions[0]
            assert legal[action_index], "matcher witness is not a legal root"
            action = PLACEMENT_ACTIONS[action_index]
            step = game.place_current_pair_and_resolve(
                action.axis_x, action.rotation, spawn_next=False
            )
            assert step is not None and not step["game_over"]
            assert step["chain_count"] == 0, "unfired core must survive resolution"
            actions.append(action_index)
        assert chosen_key is not None
        complete = _complete_for(catalog, _public_board(game), chosen_key)
        assert complete is case["expected_completed"], case["id"]
        if complete:
            assert len(actions) == case["expected_decisions"], case["id"]
        for negative_name in (
            "negative_rows_bottom_up",
            "forbidden_equal_rows_bottom_up",
        ):
            negative = _public_board(_game(case[negative_name]))
            assert not _any_complete_for(
                catalog, negative, case["template_id"], case["variant_id"]
            ), (case["id"], negative_name)
        if case["template_id"] == "gtr":
            binding = dict(chosen_key[3])
            assert binding["A"] == binding["C"] != binding["B"]
        output.append(
            {
                "id": case["id"],
                "start_rows_bottom_up": case["start_rows_bottom_up"],
                "public_pieces": case["public_pieces"],
                "selection": list(chosen_key[:3]),
                "binding": dict(chosen_key[3]),
                "statuses": statuses,
                "witness_actions": actions,
                "decisions": len(actions),
                "complete": complete,
                "final_rows_bottom_up": [
                    "".join(str(COLOR_TO_WIRE[p.color]) for p in row)
                    for row in game.field.grid[:4]
                ],
            }
        )
    assert {item["selection"][0] for item in output if item["complete"]} == {
        "gtr",
        "daa",
        "persian",
    }
    return {"catalog_digest": catalog.semantic_digest, "cases": output}


if __name__ == "__main__":
    print(json.dumps(run_cases(), ensure_ascii=False, indent=2))
