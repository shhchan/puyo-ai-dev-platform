#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
python_bin="${PUYO_NATIVE_PYTHON:-$project_root/.venv/bin/python}"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "deep-chain native release builds support Linux x86_64 only" >&2
    exit 2
fi

if [[ ! -x "$python_bin" ]]; then
    if ! command -v python3.12 >/dev/null 2>&1; then
        echo "CPython 3.12 is required" >&2
        exit 2
    fi
    python3.12 -m venv "$project_root/.venv"
fi

python_version="$($python_bin -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$python_version" != "3.12" ]]; then
    echo "CPython 3.12 is required; found $python_version" >&2
    exit 2
fi

user_directory="$(getent passwd "$(id -u)" | cut -d: -f6)"
cargo_home_directory="${CARGO_HOME:-$user_directory/.cargo}"
rustup_home_directory="${RUSTUP_HOME:-$user_directory/.rustup}"
rust_bin_directory="$cargo_home_directory/bin"
if [[ -x "$rust_bin_directory/rustup" ]]; then
    PATH="$rust_bin_directory:$PATH"
    export PATH
fi

if ! command -v rustup >/dev/null 2>&1; then
    bootstrap_directory="$(mktemp -d)"
    cleanup_bootstrap() {
        rm -f -- "$bootstrap_directory/rustup-init.sh"
        rmdir -- "$bootstrap_directory" 2>/dev/null || true
    }
    trap cleanup_bootstrap EXIT
    curl --proto '=https' --tlsv1.2 -sSf \
        https://sh.rustup.rs -o "$bootstrap_directory/rustup-init.sh"
    sh "$bootstrap_directory/rustup-init.sh" \
        -y --profile minimal --default-toolchain none
    PATH="$rust_bin_directory:$PATH"
    export PATH
fi

cd "$project_root"
rustup show active-toolchain >/dev/null
source_revision="$(git rev-parse HEAD 2>/dev/null || true)"
if [[ -z "$source_revision" ]]; then
    source_revision="unknown"
elif [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
    source_revision="$source_revision-dirty"
fi
PUYO_NATIVE_SOURCE_REVISION="$source_revision"
export PUYO_NATIVE_SOURCE_REVISION
source_date_epoch="$(git show -s --format=%ct HEAD 2>/dev/null || printf '0')"
"$python_bin" -m pip install \
    --disable-pip-version-check \
    --requirement requirements-native.txt
python_bin_directory="$(dirname -- "$python_bin")"
if [[ ! -e "$python_bin_directory/zig" && -x "$python_bin_directory/python-zig" ]]; then
    ln -s -- "$python_bin_directory/python-zig" "$python_bin_directory/zig"
fi
PATH="$python_bin_directory:$PATH"
export PATH

wheel_directory="$project_root/dist/native"
mkdir -p -- "$wheel_directory"
rustflag_separator=$'\x1f'
release_rustflags="--remap-path-prefix=$project_root=/workspace/puyo_ai_dev_platform"
release_rustflags+="$rustflag_separator--remap-path-prefix=$cargo_home_directory=/toolchains/cargo"
release_rustflags+="$rustflag_separator--remap-path-prefix=$rustup_home_directory=/toolchains/rustup"
CARGO_ENCODED_RUSTFLAGS="$release_rustflags" SOURCE_DATE_EPOCH="$source_date_epoch" \
    "$python_bin" -m maturin build \
    --release \
    --locked \
    --zig \
    --compatibility manylinux_2_28 \
    --interpreter "$python_bin" \
    --manifest-path native/deep_chain_native/Cargo.toml \
    --out "$wheel_directory"

shopt -s nullglob
wheels=("$wheel_directory"/puyo_deep_chain_native-*-cp312-*-manylinux_2_28_x86_64.whl)
if (( ${#wheels[@]} != 1 )); then
    echo "expected one CPython 3.12 manylinux_2_28_x86_64 wheel" >&2
    exit 3
fi
"$python_bin" -m pip install \
    --disable-pip-version-check \
    --force-reinstall \
    "${wheels[0]}"

"$python_bin" - <<'PY'
import _puyo_deep_chain_native as native

assert native.ABI_VERSION == 1
assert native.SCHEMA_MAJOR == 1
assert native.capabilities()[:4] == b"PDCN"
PY

sha256sum "${wheels[0]}"
