#!/usr/bin/env python3
"""Build and check the Japanese mathematical specification (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent
VERSION = "0.17.0"
ARCHIVE_URL = (
    "https://github.com/tectonic-typesetting/tectonic/releases/download/"
    "tectonic%400.17.0/tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz"
)
ARCHIVE_SHA = "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7"
BUNDLE = "https://relay.fullyjustified.net/default_bundle_v33.tar"
EPOCH = "1791849600"  # 2026-10-13 00:00:00 UTC


def bootstrap() -> Path:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("--bootstrap supports Linux x86_64; install Tectonic 0.17.0 separately.")
    destination = ROOT / ".tools" / "tectonic"
    with urllib.request.urlopen(ARCHIVE_URL, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA:
        raise SystemExit("Tectonic archive checksum mismatch")
    # Extract exactly one regular file, never archive paths or links.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = archive.getmember("tectonic")
        if not member.isfile():
            raise SystemExit("Unexpected executable in Tectonic archive")
        stream = archive.extractfile(member)
        assert stream is not None
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(stream.read())
    destination.chmod(0o755)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="install verified local Linux binary")
    parser.add_argument("--tectonic", help="path to Tectonic 0.17.0")
    parser.add_argument("--offline", action="store_true", help="use previously downloaded TeX resources")
    args = parser.parse_args()
    if args.bootstrap and args.offline:
        parser.error("--bootstrap requires network access and cannot be combined with --offline")
    binary = args.tectonic or shutil.which("tectonic")
    if args.bootstrap:
        binary = str(bootstrap())
    if binary is None and (ROOT / ".tools/tectonic").is_file():
        binary = str(ROOT / ".tools/tectonic")
    if binary is None:
        raise SystemExit("Install Tectonic 0.17.0 or run build.py --bootstrap")
    binary = str(Path(binary).resolve())
    version = subprocess.check_output([binary, "--version"], text=True).strip()
    if version != f"Tectonic {VERSION}":
        raise SystemExit(f"Expected Tectonic {VERSION}, got {version}")
    subprocess.run([os.sys.executable, str(ROOT / "verify.py")], check=True)
    output = ROOT / "build"
    output.mkdir(exist_ok=True)
    command = [binary, "--bundle", BUNDLE, "--keep-logs", "--keep-intermediates",
               "-Z", "deterministic-mode", "--outdir", str(output), "main.tex"]
    if args.offline:
        command.insert(1, "--only-cached")
    env = dict(os.environ, SOURCE_DATE_EPOCH=EPOCH)
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    log = (output / "main.log").read_text(errors="replace")
    errors = re.findall(
        r"^.*(?:Undefined control sequence|undefined references|Reference .+ undefined|"
        r"Citation .+ undefined|multiply defined|Missing character|Overfull \\[hv]box|"
        r"Rerun to get cross-references right).*$", log, flags=re.MULTILINE,
    )
    if errors:
        raise SystemExit("Document checks failed:\n" + "\n".join(errors))
    pdf = output / "main.pdf"
    if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
        raise SystemExit("No PDF produced")
    shutil.copyfile(pdf, ROOT / "nextgen-model.pdf")
    metadata = {
        "engine": version, "bundle_url": BUNDLE, "source_date_epoch": EPOCH,
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(ROOT.glob("*.tex"))},
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "checks": "references, citations, missing glyphs, overflow: passed",
    }
    (output / "build-manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Built and checked: {ROOT / 'nextgen-model.pdf'}")


if __name__ == "__main__":
    main()
