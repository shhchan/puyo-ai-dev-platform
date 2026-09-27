"""Run the PUYO-273 GUI probe with optional PUYO-269 reference behavior."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

from eval.puyo_273_gui_probe import main


REFERENCE_SHA = "f53252bbd4f0c526a6a4ab3ea497eae96fce1e02"


def use_reference_behavior() -> str:
    from eval.realtime_versus_ui import RealtimeHumanController
    from puyo_env.nextgen_scheduler import NextgenScheduler

    source = subprocess.check_output(
        ["git", "show", f"{REFERENCE_SHA}:puyo_env/nextgen_scheduler.py"],
        text=True,
    )
    module = types.ModuleType("puyo_269_reference_scheduler")
    exec(compile(source, f"{REFERENCE_SHA}:puyo_env/nextgen_scheduler.py", "exec"), module.__dict__)
    for name in ("accept", "finish"):
        setattr(NextgenScheduler, name, getattr(module.NextgenScheduler, name))

    current_next_input = RealtimeHumanController.next_input

    def reference_next_input(self, *args, **kwargs):
        return replace(current_next_input(self, *args, **kwargs), edges=())

    RealtimeHumanController.next_input = reference_next_input
    return hashlib.sha256(source.encode()).hexdigest()


if __name__ == "__main__":
    reference = "--reference-269" in sys.argv
    reference_source_sha256 = None
    if reference:
        sys.argv.remove("--reference-269")
        reference_source_sha256 = use_reference_behavior()
    main()
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    result = json.loads(output.read_text())
    result["puyo_269_reference"] = {
        "enabled": reference,
        "source_revision": REFERENCE_SHA if reference else None,
        "scheduler_source_sha256": reference_source_sha256,
        "human_edges": "legacy_release_then_press" if reference else "ordered",
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
