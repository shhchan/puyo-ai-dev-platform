"""固定した8seed×2 repeatsをbaseline/candidate交互・直列で実行する。"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
WORK = ROOT.parent
CONDITIONS = (("baseline", WORK / "baseline"), ("candidate2", WORK / "puyo-240"))
SEEDS = (123, 126, 130, 133, 135, 138, 147, 151)


def invoke(command, name, checkout, *extra):
    subprocess.run(
        [str(checkout / ".venv/bin/python"), str(ROOT / "trial.py"), command,
         str(ROOT / name), *map(str, extra)],
        cwd=checkout, env={**os.environ, "PYTHONPATH": "."}, check=True,
    )


for name, checkout in CONDITIONS:
    invoke("init", name, checkout)
for seed in SEEDS:
    for repeat in (1, 2):
        for name, checkout in CONDITIONS:
            invoke("worker", name, checkout, "--seed", seed, "--repeat", repeat)
for repeat in (1, 2, 3):
    for name, checkout in CONDITIONS:
        invoke("fixed", name, checkout, "--repeat", repeat)
