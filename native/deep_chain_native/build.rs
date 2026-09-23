use std::env;
use std::path::Path;
use std::process::Command;

fn command_output(program: &str, args: &[&str], directory: Option<&Path>) -> Option<String> {
    let mut command = Command::new(program);
    command.args(args);
    if let Some(directory) = directory {
        command.current_dir(directory);
    }
    let output = command.output().ok()?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn repository_root(manifest_dir: &Path) -> Option<&Path> {
    manifest_dir
        .ancestors()
        .find(|candidate| candidate.join(".git").exists())
}

fn source_revision(manifest_dir: &Path) -> String {
    if let Ok(value) = env::var("PUYO_NATIVE_SOURCE_REVISION")
        && !value.trim().is_empty()
    {
        return value;
    }
    let Some(root) = repository_root(manifest_dir) else {
        return "unknown".to_owned();
    };
    let Some(mut revision) = command_output("git", &["rev-parse", "HEAD"], Some(root)) else {
        return "unknown".to_owned();
    };
    let dirty = command_output(
        "git",
        &["status", "--porcelain", "--untracked-files=normal"],
        Some(root),
    )
    .is_some_and(|output| !output.is_empty());
    if dirty {
        revision.push_str("-dirty");
    }
    revision
}

fn emit_git_rerun_paths(manifest_dir: &Path) {
    let Some(root) = repository_root(manifest_dir) else {
        return;
    };
    let git_directory = root.join(".git");
    if !git_directory.is_dir() {
        return;
    }
    let head = git_directory.join("HEAD");
    println!("cargo:rerun-if-changed={}", head.display());
    println!(
        "cargo:rerun-if-changed={}",
        git_directory.join("index").display()
    );
    if let Ok(contents) = std::fs::read_to_string(head)
        && let Some(reference) = contents.trim().strip_prefix("ref: ")
    {
        println!(
            "cargo:rerun-if-changed={}",
            git_directory.join(reference).display()
        );
    }
}

fn main() {
    println!("cargo:rerun-if-env-changed=PUYO_NATIVE_SOURCE_REVISION");
    println!("cargo:rerun-if-changed=build.rs");
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR is set");
    let manifest_dir = Path::new(&manifest_dir);
    emit_git_rerun_paths(manifest_dir);
    let rustc = env::var("RUSTC").unwrap_or_else(|_| "rustc".to_owned());
    let compiler =
        command_output(&rustc, &["--version"], None).unwrap_or_else(|| "rustc unknown".to_owned());
    let profile = env::var("PROFILE").unwrap_or_else(|_| "unknown".to_owned());
    let target = env::var("TARGET").unwrap_or_else(|_| "unknown".to_owned());
    println!(
        "cargo:rustc-env=PUYO_NATIVE_SOURCE_REVISION={}",
        source_revision(manifest_dir)
    );
    println!("cargo:rustc-env=PUYO_NATIVE_COMPILER={compiler}");
    println!("cargo:rustc-env=PUYO_NATIVE_BUILD_PROFILE={profile}");
    println!("cargo:rustc-env=PUYO_NATIVE_TARGET={target}");
}
