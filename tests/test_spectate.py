import io
import unittest


try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from eval.spectate import render_board, run_spectated_match
    from selfplay.policies import FirstLegalPolicy
    from src.core.game import GameState

    SPECTATE_AVAILABLE = True
except Exception:
    SPECTATE_AVAILABLE = False
    render_board = None
    run_spectated_match = None
    FirstLegalPolicy = None
    GameState = None


@unittest.skipUnless(SPECTATE_AVAILABLE, "gymnasium/numpy are not installed")
class TestSpectate(unittest.TestCase):
    def test_render_board_has_visible_rows(self):
        rows = render_board(GameState(seed=1))

        self.assertEqual(len(rows), 12)
        self.assertTrue(all(len(row) == 6 for row in rows))

    def test_spectated_match_prints_two_boards(self):
        output = io.StringIO()
        result = run_spectated_match(
            FirstLegalPolicy(),
            FirstLegalPolicy(),
            seed=1,
            max_steps=2,
            output=output,
        )

        text = output.getvalue()
        self.assertIn("P0      P1", text)
        self.assertIn("all_clear=(empty=1,achieved=0,pending=0,consumed=0)", text)
        self.assertIn("rewards:", text)
        self.assertEqual(result["steps"], 2)


if __name__ == "__main__":
    unittest.main()
