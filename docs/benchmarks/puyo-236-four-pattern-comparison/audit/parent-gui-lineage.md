# GUI lineage and pending scope

Parent rechecked on 2026-09-10 before measurement:

- Existing PUYO-235 normal-window human observations refer to runtime commit `c1267a9d70c09c8257f4fe2546d3daee3b4424eb`, release wheel SHA `9887b2e34b3ad8ae817df585003985a3686e3d13de61c77ad5ec9c21e43be146`, seed187/reference/native/target10.
- `git diff --stat c1267a9d70c09c8257f4fe2546d3daee3b4424eb 73ab4e8ce066555042f1a20e1b3b59be3a2a8968 -- agents native train/config src puyo_env eval/realtime_versus_ui.py` exited0 with no differences. This establishes unchanged relevant runtime source, not a new human observation or identical rebuilt wheel bytes.
- At the immutable prior baseline worktree `/home/sion2/workspaces/puyo-small-quality-20260909/baseline`, `.venv/bin/python -m eval.deep_chain_builder_benchmark verify-gui-qa --output-dir docs/benchmarks/puyo-235-normal-window-qa` exited0: `{"issues": [], "passed": true}`. This command rechecks existing stored evidence without changing it.
- Source instructions reviewed: `docs/development/puyo-235-normal-window-qa.md`; automated replay validation does not substitute for visual readability, ghost consistency, overlay OFF/ON recovery, pause/resume and responsiveness.

For new only240, only242 and both arms, search/ranking/plan behavior changes. Their new human normal-window QA is **pending**. Related automated/dummy checks, when run, must be recorded separately and cannot make these human checks PASS. This does not prevent gathering the authorized headless comparative decision material. Adoption and any selected-arm human QA happen after the user's pattern choice.

For the none arm, the old human observation has unchanged-runtime lineage, but this session still makes no claim of a new normal-window run on the new build/environment.

No GUI was launched for a new human observation by the parent in this check, and no prior artifact was edited.
