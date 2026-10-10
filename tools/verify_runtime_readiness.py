#!/usr/bin/env python3
"""Capture fresh real-CLI check/export/snapshot proofs without replacing old proofs."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"core": (81, 62123296), "font-fusion-pixel": (42, 162339786), "font-ark-pixel": (42, 59321705)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    proof = Path(tempfile.mkdtemp(prefix="runtime-readiness-", dir=ROOT / ".source-cache"))
    commands = []
    def run(*argv):
        proc = subprocess.run([str(binary), *map(str, argv)], capture_output=True, text=True)
        commands.append({"argv": [str(binary), *map(str, argv)], "exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
        (proof / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if proc.returncode:
            raise RuntimeError(f"CLI failure: {argv}: {proc.stderr}; proof={proof}")
        return proc.stdout.strip()
    results = {}
    for name, expected in EXPECTED.items():
        package = ROOT / "packages" / name
        run("app", "check", package)
        run("app", "build", package)
        source_snapshot = run("app", "snapshot-hash", package)
        exported = proof / name
        run("app", "package", package, exported)
        run("app", "check", exported)
        exported_snapshot = run("app", "snapshot-hash", exported)
        manifest = json.loads((package / "manifest.json").read_text())
        declared = {r["path"] for r in manifest["resources"]}
        actual = {str(p.relative_to(exported)) for p in exported.rglob("*.bdf")}
        assert actual == declared, (name, actual ^ declared)
        bdf_bytes = sum((exported / path).stat().st_size for path in actual)
        assert (len(actual), bdf_bytes) == expected
        hashes = {path: hashlib.sha256((exported / path).read_bytes()).hexdigest() for path in sorted(actual)}
        assert all(digest == hashlib.sha256((package / path).read_bytes()).hexdigest() for path, digest in hashes.items())
        allowed = declared | set(manifest["license_files"]) | {"manifest.json"}
        if manifest.get("entry"):
            allowed.add(manifest["entry"])
        assert {str(p.relative_to(exported)) for p in exported.rglob("*") if p.is_file()} == allowed
        results[name] = {"BDF_count": len(actual), "BDF_bytes": bdf_bytes,
                         "manifest_bytes": (package / "manifest.json").stat().st_size,
                         "source_all_file_bytes": sum(p.stat().st_size for p in package.rglob("*") if p.is_file()),
                         "export_all_file_bytes": sum(p.stat().st_size for p in exported.rglob("*") if p.is_file()),
                         "source_snapshot_sha256": source_snapshot, "export_snapshot_sha256": exported_snapshot,
                         "original_BDF_sha256": hashes}
    report = {"binary": str(binary), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "results": results, "scope": "actual local CLI; source includes inventory/README, declared-only exports intentionally do not"}
    (proof / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: {k: v for k, v in row.items() if k != "original_BDF_sha256"} for name, row in results.items()}, indent=2))
    print(f"proof: {proof}")


if __name__ == "__main__":
    main()
