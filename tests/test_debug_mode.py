import unittest

try:
    import main as main_module

    MAIN_AVAILABLE = True
except ModuleNotFoundError:
    main_module = None
    MAIN_AVAILABLE = False


@unittest.skipUnless(MAIN_AVAILABLE, "main module dependencies are not installed")
class TestDebugCli(unittest.TestCase):
    def test_debug_flag_defaults_to_false(self):
        args = main_module.parse_cli_args([])
        self.assertFalse(args.debug)

    def test_long_debug_flag_sets_true(self):
        args = main_module.parse_cli_args(["--debug"])
        self.assertTrue(args.debug)

    def test_short_debug_flag_sets_true(self):
        args = main_module.parse_cli_args(["-d"])
        self.assertTrue(args.debug)


if __name__ == "__main__":
    unittest.main()
