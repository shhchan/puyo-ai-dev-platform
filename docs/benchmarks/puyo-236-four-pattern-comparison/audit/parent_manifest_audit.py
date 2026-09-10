"""Read-only independent inspection of the frozen four-arm declaration."""

import argparse
import collections
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(checkout, *args):
    return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("declaration_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    declared_path = args.declaration_dir / "four-arm-manifest.json"
    declared = json.loads(declared_path.read_text())
    schedule = declared["schedule"]
    expected = {(a, s, r) for a in ("none", "only240", "only242", "both") for s in range(123, 153) for r in (1, 2)}
    check(len(schedule) == 240 and {(e["arm"], e["seed"], e["repeat"]) for e in schedule} == expected, "Invalid schedule")
    for arm in ("none", "only240", "only242", "both"):
        check(collections.Counter(e["position"] for e in schedule if e["arm"] == arm) == {p: 15 for p in range(4)}, "Unbalanced order")
    baseline = "73ab4e8ce066555042f1a20e1b3b59be3a2a8968"
    old = {"only240": "2e1fe2b01ac4800dc256f2cf4fcdc009e9fb09a8", "only242": "04919bb9d102fce0af6a963d8b7d0a3b98c467db"}
    arms, common, dependencies = {}, [], []
    for arm in ("none", "only240", "only242", "both"):
        path = args.declaration_dir / arm / "experiment_manifest.json"
        manifest = json.loads(path.read_text())
        checkout = workspace / arm
        head = git(checkout, "rev-parse", "HEAD")
        check(head == manifest["build_provenance"]["evaluated_commit"], f"HEAD mismatch: {arm}")
        check(not git(checkout, "status", "--porcelain", "--untracked-files=no"), f"Dirty source: {arm}")
        check(manifest["build_provenance"]["capabilities"]["source_revision"] == head, f"Wheel revision: {arm}")
        check(all(manifest["build_provenance"]["checks"].values()), f"Provenance failure: {arm}")
        check(manifest["build_provenance"]["capabilities"]["build_profile"] == "release", f"Not release: {arm}")
        check(manifest["runtime"]["source_tree"] == git(checkout, "rev-parse", "HEAD^{tree}"), f"Tree mismatch: {arm}")
        check(manifest["manifest_sha256"] == declared["arm_manifest_sha256"][arm], f"Declaration mismatch: {arm}")
        for name, expected_sha in manifest["runtime"]["runtime_file_sha256"].items():
            check(sha(checkout / name) == expected_sha, f"Runtime SHA: {arm}/{name}")
        wheel = manifest["build_provenance"]["wheels"][0]
        check(sha(checkout / wheel["path"]) == wheel["sha256"], f"Wheel SHA: {arm}")
        extensions = list((checkout / ".venv/lib/python3.12/site-packages/_puyo_deep_chain_native").glob("_puyo_deep_chain_native*.so"))
        check(len(extensions) == 1 and sha(extensions[0]) == manifest["runtime"]["native_extension_sha256"], f"Installed extension SHA: {arm}")
        with zipfile.ZipFile(checkout / wheel["path"]) as archive:
            names = [name for name in archive.namelist() if name.endswith(".so")]
            check(len(names) == 1 and hashlib.sha256(archive.read(names[0])).hexdigest() == sha(extensions[0]), f"Wheel/installed extension mismatch: {arm}")
        runner = manifest["runner"]
        check(runner["commit"] == git(workspace / "control", "rev-parse", "HEAD"), "Runner HEAD mismatch")
        for name, expected_sha in runner["files"].items():
            check(sha(workspace / "control" / name) == expected_sha, f"Runner SHA: {name}")
        if arm == "none":
            check(head == baseline, "Baseline changed")
        elif arm in old:
            check(not git(checkout, "diff", old[arm], head, "--", "agents", "native", "train/config"), f"Single-arm runtime changed from old trial: {arm}")
        check(not git(checkout, "diff", baseline, head, "--", "train/config", "src", "puyo_env", "selfplay"), f"Unrelated runtime changed: {arm}")
        check(sha(checkout / "eval/deep_chain_native_corpus.json") == "4027c48d482b21547f6b1d0b554ad949e73439de4a4e09c71a503d2ca722bed4", f"Historical corpus changed: {arm}")
        common.append(manifest["common_configuration"])
        dependencies.append([d for d in manifest["runtime"]["dependencies"] if not d.startswith("puyo-deep-chain-native")])
        arms[arm] = {"commit": head, "tree": manifest["runtime"]["source_tree"], "wheel_sha256": wheel["sha256"],
                     "native_extension_sha256": manifest["runtime"]["native_extension_sha256"], "manifest_file_sha256": sha(path),
                     "manifest_identity": manifest["manifest_sha256"], "result_schema_digest": manifest["build_provenance"]["capabilities"]["schema"]["result_digest"],
                     "passed": True}
    check(all(c == common[0] for c in common), "Mixed effective configuration")
    check(all(d == dependencies[0] for d in dependencies), "Mixed dependency versions")
    profile = common[0]["profile"]
    check((profile["depth"], profile["width"], profile["scenarios"], profile["max_expanded_nodes"]) == (16, 250, 6, 600000), "Search budget changed")
    check(common[0]["target_chain_count"] == 10 and common[0]["max_steps"] == 40, "Target/placement condition changed")
    result = {"schema": "puyo236.parent_manifest_audit.v1", "passed": True, "declaration_file_sha256": sha(declared_path),
              "declaration_dir": str(args.declaration_dir), "runner": runner, "arms": arms,
              "common_configuration": common[0], "common_dependencies": dependencies[0],
              "schedule_count": 240, "positions_per_arm": [15, 15, 15, 15],
              "scope": "Source/build/config/schedule correspondence only; QA/reproduction results reviewed separately; not measurement launch approval or adoption."}
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"passed": True, "output": str(args.output), "arms": arms}))


if __name__ == "__main__":
    main()
