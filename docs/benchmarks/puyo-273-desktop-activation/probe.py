"""Same-source GUI A/B restoring only the pre-change planner when requested."""

import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import types

BASELINE = "c812b3f8ad257645937fa6140412a6db6be29930"


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
    runpy.run_module("eval.puyo_269_gui_probe", run_name="__main__")
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    data = json.loads(output.read_text())
    data["activation_reference"] = {
        "enabled": reference,
        "source_revision": BASELINE if reference else None,
        "planner_sha256": reference_hash,
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    output.write_text(json.dumps(data, indent=2) + "\n")
