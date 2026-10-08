"""Fixed same-source desktop matrix. No performance threshold adaptation."""
import gzip
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).parent
PYTHON = '/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python'
for stage in ('before', 'after'):
    for scenario in (('one', 'two', 'human', 'light') if stage == 'before' else
                     ('one', 'two', 'human', 'light', 'repeat-two', 'minimal-two', 'minimal-one', 'minimal-light')):
        name = f'{stage}-{scenario}'
        output = Path('/tmp') / f'puyo269-final-{name}.json'
        command = [PYTHON, 'eval/puyo_269_gui_probe.py', '--overlay', '--output', str(output)]
        if stage == 'before':
            command += ['--reference-wire']
        if 'minimal' in scenario:
            command += ['--minimal']
        if scenario.endswith('two'):
            command += ['--opponent', 'nextgen_tactic_manager', '--frames', '360']
        elif scenario == 'human':
            command += ['--opponent', 'human']
        elif scenario.endswith('light'):
            command += ['--policy', 'first']
        with (ROOT / f'{name}.log').open('w') as log:
            subprocess.run(command, env={**os.environ, 'PYTHONPATH': '.', 'DISPLAY': ':0',
                                         'SDL_AUDIODRIVER': 'dummy'}, stdout=log, stderr=log, check=True)
        (ROOT / 'raw' / f'{name}.json.gz').write_bytes(gzip.compress(output.read_bytes(), mtime=0))
        print(name, flush=True)
