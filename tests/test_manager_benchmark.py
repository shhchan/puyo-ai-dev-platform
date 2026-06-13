import unittest

from eval.manager_benchmark import BASELINES, parse_args


class TestManagerBenchmark(unittest.TestCase):
    def test_defaults_cover_puyo_51_matrix(self):
        args = parse_args(["--checkpoint", "manager.pt", "--output-dir", "results"])

        self.assertEqual(args.games, 50)
        self.assertEqual(args.workers, 8)
        self.assertEqual(args.beam_depth, 10)
        self.assertEqual(args.beam_width, 48)
        self.assertEqual(tuple(args.baselines), BASELINES)


if __name__ == "__main__":
    unittest.main()
