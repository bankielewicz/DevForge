//! Bind the executable to the exact content of its build inputs.
//!
//! The digest covers the manifest, lock file, this script, the Rust sources,
//! the embedded test harness and the embedded delivery runtime. It is a
//! content identity, not a Git revision: a dirty tree yields its own digest,
//! and two builds of identical inputs share one. Compilation alone does not
//! protect anything; an owner pins this value in an authority record outside
//! the evaluated agent's writable boundary.
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

fn collect(dir: &Path, files: &mut Vec<PathBuf>) {
    let mut entries: Vec<PathBuf> = fs::read_dir(dir)
        .map(|items| {
            items
                .filter_map(|item| item.ok().map(|item| item.path()))
                .collect()
        })
        .unwrap_or_default();
    entries.sort();
    for path in entries {
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or_default();
        if name == "__pycache__" || name.ends_with(".pyc") {
            continue;
        }
        if path.is_dir() {
            collect(&path, files);
        } else {
            files.push(path);
        }
    }
}

fn main() {
    let root = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let mut files: Vec<PathBuf> = ["Cargo.toml", "Cargo.lock", "build.rs"]
        .iter()
        .map(|name| root.join(name))
        .collect();
    for dir in ["src", "runners", "runtime/delivery"] {
        let dir = root.join(dir);
        println!("cargo:rerun-if-changed={}", dir.display());
        collect(&dir, &mut files);
    }
    files.sort();
    files.dedup();
    let mut identity = Sha256::new();
    for path in &files {
        println!("cargo:rerun-if-changed={}", path.display());
        let relative = path
            .strip_prefix(&root)
            .expect("build input under manifest dir")
            .to_str()
            .expect("UTF-8 build input path")
            .replace('\\', "/");
        let bytes = fs::read(path)
            .unwrap_or_else(|error| panic!("cannot read build input {}: {error}", path.display()));
        identity.update(relative.as_bytes());
        identity.update([0]);
        identity.update(format!("{:x}", Sha256::digest(&bytes)).as_bytes());
        identity.update([0]);
    }
    println!(
        "cargo:rustc-env=DEVFORGE_SOURCE_SHA256={:x}",
        identity.finalize()
    );
}
