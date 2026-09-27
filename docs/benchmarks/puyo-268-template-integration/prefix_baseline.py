"""Remeasure immutable pre-prefix source with the caller's native environment."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[3]
    source = subprocess.check_output(['git', 'rev-parse', args.source_sha], cwd=root, text=True).strip()
    archive = subprocess.check_output(['git', 'archive', source, 'agents', 'puyo_env', 'src', 'eval', 'train', 'selfplay', 'human_data'], cwd=root)
    with tempfile.TemporaryDirectory(prefix='puyo268-before-') as directory:
        exported = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(exported, filter='data')
        files = {str(p.relative_to(exported)): hashlib.sha256(p.read_bytes()).hexdigest() for p in exported.rglob('*') if p.is_file()}
        sys.path.insert(0, directory)
        os.chdir(directory)
        import _puyo_deep_chain_native as native
        from eval.nextgen_safe_build_diagnostic import measure
        from agents.deep_chain_search_backend import NativeLongHorizonSearchBackend
        assert Path(sys.modules[measure.__module__].__file__).is_relative_to(exported)
        manifest = {'source_sha': source, 'files_sha256': files,
                    'native_binary_sha256': hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
                    'native': NativeLongHorizonSearchBackend().describe(),
                    'python': sys.executable, 'platform': platform.platform(), 'affinity': sorted(os.sched_getaffinity(0)),
                    'conditions': '55/123/124; 40 placements; configured inference0; inactive opponent; fresh policy per seed'}
        results = []
        for seed in (55, 123, 124):
            run = measure('nextgen', seed, 'nextgen_safe_build', placements=40)
            path = output/f'nextgen-{seed}-40.json.gz'
            path.write_bytes(gzip.compress(json.dumps(run, allow_nan=False).encode(), mtime=0))
            results.append({k: run[k] for k in ('seed','max_chain','premature','game_over','placements','decision_seconds','semantic_digest','errors')})
        manifest['results'] = results
        manifest['artifacts_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.gz')}
        (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        os.chdir(root)


if __name__ == '__main__':
    main()
