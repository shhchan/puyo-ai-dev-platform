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


CADENCE_REFERENCE_SHA = "0a364aa2a6d8b5a5ca0313c5aa6ca3369a21f222"


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
    reference_wire = "--reference-wire" in sys.argv
    if reference_wire:
        sys.argv.remove("--reference-wire")
        import puyo_env.nextgen_scheduler as scheduler
        source = subprocess.check_output(
            ["git", "show", f"{CADENCE_REFERENCE_SHA}:puyo_env/nextgen_scheduler.py"], text=True
        )
        exec(compile(source, "puyo_269_wire_reference", "exec"), vars(scheduler))
    ui_decode = "--ui-decode" in sys.argv
    if ui_decode:
        sys.argv.remove("--ui-decode")
        import puyo_env.nextgen_scheduler as scheduler
        scheduler.decode_nextgen_payload = lambda payload: payload
    legacy_render = "--legacy-render" in sys.argv
    if legacy_render:
        sys.argv.remove("--legacy-render")
        import src.ui.versus_renderer as renderer
        from src.ui.nextgen_display import nextgen_receipt_summary
        from puyo_env.realtime_ai import RealtimeControllerDiagnostics

        def legacy_summary(payload, last_decision):
            # Reproduce the old per-frame full copy with the same decision.
            # Other controller counters are not read by the receipt summary.
            diagnostics = RealtimeControllerDiagnostics(last_decision=last_decision)
            return nextgen_receipt_summary(payload, diagnostics.to_dict())

        renderer.live_nextgen_receipt_summary = legacy_summary
    reference = "--reference-269" in sys.argv
    reference_source_sha256 = None
    if reference:
        sys.argv.remove("--reference-269")
        reference_source_sha256 = use_reference_behavior()
    main()
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    result = json.loads(output.read_text())
    result["wire_reference"] = CADENCE_REFERENCE_SHA if reference_wire else None
    result["legacy_render"] = legacy_render
    result["ui_decode"] = ui_decode
    result["puyo_269_reference"] = {
        "enabled": reference,
        "source_revision": REFERENCE_SHA if reference else None,
        "scheduler_source_sha256": reference_source_sha256,
        "human_edges": "legacy_release_then_press" if reference else "ordered",
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
