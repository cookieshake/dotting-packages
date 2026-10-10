#!/usr/bin/env python3
"""Audit the public index, then independently read back and install a remote pin."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT.parent / "dotting"
REMOTE = "https://github.com/cookieshake/dotting-packages.git"
EXPECTED_COUNTS = {"core": 6, "fonts-extra": 72, "font-fusion-pixel": 42, "font-ark-pixel": 28}
POINTER = re.compile(rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n\Z")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--remote-commit")
    parser.add_argument("--binary", type=Path, default=RUNTIME / "target/debug/dotting")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()
    assert args.index != bool(args.remote_commit), "choose --index or --remote-commit"
    assert 1 <= args.timeout_seconds <= 600, "install timeout must be between 1 and 600 seconds"
    proof = Path(tempfile.mkdtemp(prefix="packages-publication-", dir=RUNTIME / "artifacts"))
    commands = []
    def run(argv, cwd=ROOT, env=None, timeout=600):
        proc = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, capture_output=True, timeout=timeout)
        text = proc.stdout.decode(errors="replace").strip()
        commands.append({"argv": list(map(str, argv)), "cwd": str(cwd), "exit": proc.returncode,
                         "stdout": text, "stderr": re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", proc.stderr.decode(errors="replace"))})
        (proof / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if proc.returncode:
            raise RuntimeError(f"command failed ({proc.returncode}); inspect {proof}/commands.json")
        return proc.stdout
    ref = ":" if args.index else args.remote_commit + ":"
    repo = ROOT
    env = None
    if args.remote_commit:
        assert re.fullmatch(r"[0-9a-f]{40}", args.remote_commit)
        # An HTTPS clone with a new HOME/config and private fresh .git/lfs store;
        # no local references, alternates, checkout filters or cached objects.
        home = proof / "isolated-home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                   GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
        repo = proof / "independent-remote-clone"
        run(["git", "clone", "--no-checkout", "--depth", "1", REMOTE, repo], env=env)
        assert run(["git", "rev-parse", "HEAD"], cwd=repo, env=env).decode().strip() == args.remote_commit
        run(["git", "checkout", "--detach", args.remote_commit], cwd=repo, env=env)
        assert not (repo / ".git/objects/info/alternates").exists()
        assert not list((repo / ".git/lfs/objects").rglob("*")) if (repo / ".git/lfs/objects").exists() else True
    paths = run(["git", "ls-files", "-z"], cwd=repo, env=env).decode().split("\0")[:-1]
    objects = {}
    normal_bytes = 0
    largest = []
    for path in paths:
        assert not path.startswith(".source-cache/") and not any(p in path for p in (".DS_Store", "__pycache__", ".pyc")), path
        assert path in {".gitattributes", ".gitignore", "README.md"} or path.startswith(("packages/", "tools/")), path
        blob = subprocess.check_output(["git", "cat-file", "blob", ref + path], cwd=repo, env=env)
        match = POINTER.fullmatch(blob)
        if path.endswith(".bdf") and path != "packages/core/assets/fonts/tom-thumb-4x6.bdf":
            assert match, ("ordinary Git BDF instead of LFS pointer", path)
            oid, size = match[1].decode(), int(match[2])
            objects[path] = {"oid": oid, "size": size}
            if args.index:
                assert sha(ROOT / path) == oid and (ROOT / path).stat().st_size == size, path
        else:
            assert not match, path
            normal_bytes += len(blob)
            largest.append((len(blob), path))
            assert len(blob) < 16 * 1024 * 1024, path
            assert not re.search(rb"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|" + b"/" + rb"Users/[^/\s]+/", blob), ("private material suspected", path)
    assert len(objects) == 147
    report = {"mode": "index" if args.index else "independent remote clone and native installation",
              "git_commit": args.remote_commit, "Git_LFS_pointer_entries": len(objects),
              "unique_LFS_objects": len({r["oid"] for r in objects.values()}),
              "ordinary_Git_blob_bytes": normal_bytes, "largest_ordinary_blobs": sorted(largest, reverse=True)[:5]}
    (proof / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if args.remote_commit:
        # The isolated HOME intentionally has no global LFS filter setup.
        # Configure this throwaway clone only so pull also hydrates its checkout.
        run(["git", "lfs", "install", "--local"], cwd=repo, env=env)
        run(["git", "lfs", "pull"], cwd=repo, env=env)
        run(["git", "lfs", "fsck"], cwd=repo, env=env)
        for path, row in objects.items():
            assert sha(repo / path) == row["oid"] and (repo / path).stat().st_size == row["size"], path
            stored = repo / ".git/lfs/objects" / row["oid"][:2] / row["oid"][2:4] / row["oid"]
            assert stored.is_file() and sha(stored) == row["oid"], stored
        binary = args.binary.resolve(strict=True)
        report["binary_sha256"] = sha(binary)
        report["packages"] = {}
        # Empty HOME for each native install; empty PATH; no Git/LFS child tools.
        for name, count in EXPECTED_COUNTS.items():
            snapshot = run([binary, "app", "snapshot-hash", repo / "packages" / name]).decode().strip()
            output = proof / ("native-" + name)
            audit = proof / (name + "-download-audit.json")
            native_home = proof / (name + "-empty-home")
            native_home.mkdir()
            native_env = dict(env, HOME=str(native_home), PATH="")
            run([binary, "app", "install", name, "--repository", REMOTE,
                  "--commit", args.remote_commit, "--output", output,
                  "--package-path", "packages/" + name, "--sha256", snapshot,
                  "--timeout-seconds", args.timeout_seconds,
                  "--download-audit", audit], env=native_env)
            run([binary, "app", "check", output], env=native_env)
            assert run([binary, "app", "snapshot-hash", output], env=native_env).decode().strip() == snapshot
            manifest = json.loads((output / "manifest.json").read_text())
            declared = {r["path"] for r in manifest["resources"]}
            actual = {str(p.relative_to(output)) for p in output.rglob("*.bdf")}
            assert actual == declared and len(actual) == count
            bdf_bytes = sum((output / p).stat().st_size for p in actual)
            for path in actual:
                assert sha(output / path) == sha(repo / "packages" / name / path)
            events = json.loads(audit.read_text())
            selected = {p.removeprefix("packages/" + name + "/"): row for p, row in objects.items() if p.startswith("packages/" + name + "/")}
            fetched = {e["path"]: {"oid": e["oid"], "size": e["body_bytes"]} for e in events if e["kind"] == "lfs-object"}
            requested = {e["path"]: e["oid"] for e in events if e["kind"] == "lfs-batch"}
            assert fetched == selected
            assert requested == {p: row["oid"] for p, row in selected.items()}
            disk = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
            receipt_bytes = (output / ".dotting-install.json").stat().st_size
            all_bytes = sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) - receipt_bytes
            assert disk - receipt_bytes == all_bytes
            report["packages"][name] = {
                "snapshot_sha256": snapshot, "BDF_count": count, "BDF_bytes": bdf_bytes,
                "content_disk_file_bytes": disk - receipt_bytes, "receipt_bytes": receipt_bytes,
                "total_disk_file_bytes": disk, "lfs_request_count": len(requested),
                "LFS_object_download_body_bytes": sum(e["body_bytes"] for e in events if e["kind"] == "lfs-object"),
                "deduplicated_LFS_object_bytes": sum({e["oid"]: e["body_bytes"] for e in events if e["kind"] == "lfs-object"}.values()),
                "all_successful_HTTP_response_body_bytes": sum(e["body_bytes"] for e in events),
                "audit": str(audit), "wire_bytes": "not measured (no TLS/HTTP header accounting)",
            }
            (proof / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"proof: {proof}")


if __name__ == "__main__":
    main()
