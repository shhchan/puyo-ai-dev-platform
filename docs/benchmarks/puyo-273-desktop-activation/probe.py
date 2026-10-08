"""Same-source GUI A/B restoring only the pre-change planner when requested."""

import ast
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import types

BASELINE = "c812b3f8ad257645937fa6140412a6db6be29930"
ACTIVATION_BASELINE = "b7365ba"


def reference_planner():
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE}:puyo_env/action_planner.py"], text=True
    )
    module = types.ModuleType("puyo_273_reference_planner")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{BASELINE}:puyo_env/action_planner.py", "exec"), module.__dict__)
    return module, hashlib.sha256(source.encode()).hexdigest()


if __name__ == "__main__":
    reference = "--reference-plans" in sys.argv
    activation_reference = "--reference-activation" in sys.argv
    if reference and activation_reference:
        raise SystemExit("choose one reference mode")
    reference_hash = None
    if reference:
        sys.argv.remove("--reference-plans")
        import puyo_env.realtime_ai as ai

        planner, reference_hash = reference_planner()

        def reachable(source, actions, *, plan_results=None, **kwargs):
            # The old mask discarded its witnesses, so activation replanned.
            return planner.reachable_placement_actions(source, actions, **kwargs)

        ai.reachable_placement_actions = reachable
        ai.plan_placement_action = planner.plan_placement_action
        # The baseline always computed the full activation mask.
        ai.nextgen_plan_is_current = lambda *args: False
    activation_hash = None
    if activation_reference:
        sys.argv.remove("--reference-activation")
        import puyo_env.realtime_ai as ai

        source = subprocess.check_output(
            ["git", "show", f"{ACTIVATION_BASELINE}:puyo_env/realtime_ai.py"], text=True
        )
        cls = next(node for node in ast.parse(source).body
                   if isinstance(node, ast.ClassDef) and node.name == "RealtimePolicyController")
        method = next(node for node in cls.body
                      if isinstance(node, ast.FunctionDef) and node.name == "_activate_nextgen")
        namespace = dict(vars(ai))
        exec(compile(ast.Module(body=[method], type_ignores=[]), "activation_reference", "exec"), namespace)
        baseline_activate = namespace["_activate_nextgen"]

        def activate(self, match, agent, record, prepared_plan=None):
            return baseline_activate(self, match, agent, record)

        ai.RealtimePolicyController._activate_nextgen = activate
        activation_hash = hashlib.sha256(source.encode()).hexdigest()
    runpy.run_module("eval.puyo_269_gui_probe", run_name="__main__")
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    data = json.loads(output.read_text())
    data["activation_reference"] = {
        "enabled": reference or activation_reference,
        "source_revision": ACTIVATION_BASELINE if activation_reference else BASELINE if reference else None,
        "planner_sha256": reference_hash,
        "controller_sha256": activation_hash,
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    output.write_text(json.dumps(data, indent=2) + "\n")
