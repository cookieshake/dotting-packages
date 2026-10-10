#!/usr/bin/env python3
"""Audit physical package content and preserve a migration recovery ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from assemble_catalog import CORE, ROOT, STAGING, archive_unused_notices, copy_file, load, safe_source

EXPECTED_COUNTS = {"core": 6, "fonts-extra": 72, "font-fusion-pixel": 42, "font-ark-pixel": 28}
LEDGER = ROOT / ".source-cache/recovery-before.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture() -> None:
    if LEDGER.exists():
        raise ValueError("recovery ledger exists; refusing to overwrite earlier proof")
    files = [p for p in (ROOT / "packages").rglob("*") if p.is_file()]
    ledger = {str(p.relative_to(ROOT)): {"sha256": sha(p), "bytes": p.stat().st_size}
              for p in files}
    for p in files:
        if p.suffix != ".bdf":
            copy_file(p, ROOT / ".source-cache/recovery-before" / p.relative_to(ROOT))
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
    print(f"captured {len(files)} files: {LEDGER.relative_to(ROOT)}")


def recover_legacy_notices() -> None:
    legacy = ROOT / ".source-cache/unused-notices/packages/core/licenses"
    for p in legacy.rglob("*"):
        if p.is_file():
            target = ROOT / ".source-cache/notices/licenses" / p.relative_to(legacy)
            if target.exists() and sha(target) != sha(p):
                raise ValueError(f"notice staging collision: {p}")
            copy_file(p, target)


def validate() -> None:
    all_hashes = set()
    total = 0
    for name, expected_count in EXPECTED_COUNTS.items():
        package = ROOT / "packages" / name
        manifest = json.loads((package / "manifest.json").read_text())
        fonts = {r["path"]: r for r in manifest["resources"] if r["kind"] == "font"}
        metadata = manifest["metadata"]["fonts"]
        assert len(metadata) == len(fonts), (name, "duplicate/stale metadata")
        assert len({r["id"] for r in metadata}) == len(metadata), name
        actual = {str(p.relative_to(package)) for p in package.rglob("*")
                  if p.is_file() and p.suffix.lower() == ".bdf"}
        assert actual == set(fonts), (name, "undeclared/missing BDF", actual ^ set(fonts))
        sizes = [(package / p).stat().st_size for p in actual]
        assert len(actual) == expected_count, (name, len(actual), expected_count)
        for row in metadata:
            resource = next(r for r in fonts.values() if r["id"] == row["id"])
            p = package / resource["path"]
            assert sha(p) == row["sha256"], p
            if "bytes" in row:
                assert p.stat().st_size == row["bytes"], p
            all_hashes.add(sha(p))
        notices = set(manifest["license_files"])
        assert {str(p.relative_to(package)) for p in (package / "licenses").rglob("*") if p.is_file()} == notices, (name, "undeclared notice")
        resources = {r["path"] for r in manifest["resources"]}
        if manifest.get("entry"):
            resources.add(manifest["entry"])
        for p in package.rglob("*"):
            assert not p.is_symlink(), p
            if not p.is_file():
                continue
            relative = str(p.relative_to(package))
            assert (relative in {"manifest.json", "README.md"} or relative in resources or relative in notices
                    or relative.startswith(("inventory/", "widgets/"))), (name, "undeclared file", relative)
        print(f"{name}: BDF count={len(actual)} bytes={sum(sizes)} max={max(sizes)} disk_file_bytes={sum(p.stat().st_size for p in package.rglob('*') if p.is_file())}")
        total += len(actual)
    assert (total, len(all_hashes)) == (148, 145), (total, len(all_hashes))
    # Independent original/notice parity against the family source inventories.
    for f in sorted((CORE / "inventory/families").glob("*.json")):
        data = load(f.stem)
        notices = data.get("license_notices", [])
        if not notices and data.get("license_path"):
            notices = [{"path": data["license_path"], "sha256": data.get("license_sha256", data.get("license_notice_sha256"))}]
        if not notices and isinstance(data.get("license"), dict):
            notices = [{"path": f"licenses/{f.stem}/LICENSE.txt", "sha256": data["license"]["sha256"]}]
        for row in notices:
            p = CORE / row["path"]
            if not p.exists():
                p = ROOT / ".source-cache/notices" / row["path"]
            if not p.exists():
                p = ROOT / ".source-cache/unused-notices/packages/core" / row["path"]
            assert sha(p) == row["sha256"], p
        for row in data.get("fonts", data.get("files", [])):
            expected_hash = row.get("sha256", row.get("source_file_sha256"))
            if f.stem == "ark-pixel" and row.get("deprecated"):
                preserved = [p for p in (ROOT / ".source-cache/fonts/ark-pixel").rglob(Path(row["filename"]).name)
                             if p.is_file() and sha(p) == expected_hash]
                assert len(preserved) == 1, (f, row)
            else:
                assert expected_hash in all_hashes, (f, row)
    archival = json.loads((ROOT / ".source-cache/archival/ark-pixel/full-inventory.json").read_text())
    archived = [row for row in archival["fonts"] if row.get("deprecated")]
    assert len(archived) == 14 and all(row["sha256"] not in all_hashes for row in archived)
    assert not any("16px" in str(p) for p in (ROOT / "packages").rglob("*.bdf"))
    if LEDGER.exists():
        # Every pre-recovery original BDF and notice survives byte-for-byte.
        preserved = {sha(p) for base in (ROOT / "packages", ROOT / ".source-cache")
                     for p in base.rglob("*") if p.is_file()}
        ledger = json.loads(LEDGER.read_text())
        for path, row in ledger.items():
            assert row["sha256"] in preserved, ("lost original", path)
        print(f"preservation ledger: all {len(ledger)} original file hashes retained")
    print("catalog: 148 entries, 145 unique distributed BDF hashes; 14 Ark archive hashes preserved outside packages; original/license parity passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-recovery", action="store_true")
    parser.add_argument("--archive-unused-notices", action="store_true")
    parser.add_argument("--recover-legacy-notices", action="store_true")
    args = parser.parse_args()
    if args.capture_recovery:
        capture()
    else:
        if args.recover_legacy_notices:
            recover_legacy_notices()
        if args.archive_unused_notices:
            archive_unused_notices()
        validate()
