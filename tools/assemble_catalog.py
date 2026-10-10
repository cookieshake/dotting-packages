#!/usr/bin/env python3
"""Split the staged font catalog into four native packages.

The family inventories under .source-cache/inventory/families are the source of
truth. This script copies original BDF/license bytes and emits manifests using
the existing native manifest resource format; it never edits font data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import os
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "packages/core"
FAMILIES = ROOT / ".source-cache/inventory/families"
STAGING = ROOT / ".source-cache/fonts"
NOTICES = ROOT / ".source-cache/notices"
FUSION_CORE = {
    "fusion-pixel-8px-monospaced-latin.bdf",
    "fusion-pixel-10px-monospaced-latin.bdf",
    "fusion-pixel-12px-monospaced-latin.bdf",
}
CORE_FAMILIES = {"spleen", "tamzen", "bitocra", "gohu", "scientifica",
                 "galmuri", "misaki", "k8x12"}


def load(family: str) -> dict:
    if family == "spleen" and not (FAMILIES / "spleen.json").exists():
        return {"family": "Spleen", "version": "2.2.0", "license": "BSD-2-Clause",
                "license_path": "licenses/Spleen-LICENSE.txt", "fonts": []}
    return json.loads((FAMILIES / f"{family}.json").read_text())


def package_family_data(family: str) -> dict:
    data = load(family)
    if family == "ark-pixel":
        data = json.loads(json.dumps(data))
        data["fonts"] = [row for row in data.get("fonts", []) if not row.get("deprecated")]
        data["counts"]["fonts"] = len(data["fonts"])
        data["counts"]["font_bytes"] = sum(row["bytes"] for row in data["fonts"])
        data["license_notices"] = [row for row in data.get("license_notices", []) if "deprecated-16px" not in row.get("path", "")]
        data["license_path"] = data["license_notices"][0]["path"]
        data["expected"] = {"sizes_px": [10, 12], "styles": ["mono", "proportional"], "regions_per_size_style": 7}
        data["size_style_region_counts"] = {key: value for key, value in data.get("size_style_region_counts", {}).items() if key in {"10", "12"}}
    return data


def safe_source(raw: str) -> Path:
    candidate = Path(raw)
    path = (ROOT / candidate if raw.startswith(("packages/", ".source-cache/")) else CORE / candidate).resolve()
    if raw.startswith("licenses/") and not path.exists():
        path = (NOTICES / candidate).resolve()
    if not (path.is_relative_to(CORE.resolve()) or path.is_relative_to(STAGING.resolve()) or path.is_relative_to(NOTICES.resolve())):
        raise ValueError(f"source path escapes package and staging roots: {raw}")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def font_rows(family: str) -> list[dict]:
    data = package_family_data(family)
    if family == "spleen":
        from import_fonts import SOURCES
        rows = []
        for spec in SOURCES:
            if not (str(spec["path"]).startswith(".source-cache/fonts/spleen/") or spec["id"] == "spleen-32x64"):
                continue
            path = ROOT / spec["path"] if str(spec["path"]).startswith(".source-cache/") else ROOT / ".source-cache/fonts/spleen/2.2.0/spleen-32x64.bdf"
            rows.append({"filename": path.name, "path": str(path.relative_to(ROOT)),
                         "sha256": spec["sha256"],
                         "id": "fonts-spleen-" + path.stem.replace("x", "x"),
                         "version": "2.2.0", "source_url": "https://github.com/fcambus/spleen/releases/tag/2.2.0"})
        return rows
    if data.get("fonts"):
        rows = data["fonts"]
    else:
        rows = data.get("files", [])
    for row in rows:
        row.setdefault("family", family)
    return rows


def relative_source(row: dict) -> str:
    path = row.get("path") or row.get("filename")
    if not path:
        raise ValueError(f"font entry has no path: {row}")
    if path.startswith(".source-cache/fonts/"):
        return path
    if path.startswith("packages/core/fonts/"):
        return str(Path(".source-cache/fonts") / path.removeprefix("packages/core/fonts/"))
    if path.startswith("packages/core/"):
        return path
    filename = PurePosixPath(row.get("filename") or path).name
    family_dir = STAGING / row["family"]
    expected_sha = row.get("sha256")
    matches = []
    for candidate in family_dir.rglob(filename):
        if not candidate.is_file():
            continue
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if expected_sha and digest == expected_sha:
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError(
            f"expected one {row['family']} source matching filename and SHA-256 "
            f"for {filename}; found {len(matches)}"
        )
    return str(matches[0].relative_to(ROOT))


def font_id(row: dict, family: str) -> str:
    # Keep assigned catalog IDs for families already registered in core.
    existing = row.get("id")
    if existing:
        return existing
    file = PurePosixPath(row["filename"])
    bits = [part.lower().replace("px", "") for part in file.parts[:-1]]
    stem = file.stem.lower().replace("_", "-")
    return "fonts-" + "-".join([family, *bits, stem])


def license_sources(family: str) -> list[tuple[str, str]]:
    data = package_family_data(family)
    paths = []
    for notice in data.get("license_notices", []):
        paths.append((notice["path"], notice["path"]))
    for key in ("license_path",):
        if data.get(key):
            paths.append((data[key], data[key]))
    if isinstance(data.get("license"), dict):
        path = f"licenses/{family}/LICENSE.txt"
        paths.append((path, path))
    # Deduplicate while preserving order.
    return list(dict.fromkeys(paths))


def copy_file(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if source.resolve() != target.resolve():
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(fd)
        try:
            shutil.copyfile(source, temporary)
            if hashlib.sha256(Path(temporary).read_bytes()).hexdigest() != digest:
                raise ValueError(f"copy hash mismatch; source preserved: {source}")
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return digest


def migrate_legacy_staging() -> None:
    """Move legacy in-package source BDFs into ignored staging without loss."""
    legacy_root = CORE / "fonts"
    if not legacy_root.exists():
        return
    sources = sorted(legacy_root.rglob("*.bdf"))
    # Verify all copies before touching any installed source. Keep declared files
    # in place; this migration must also be safe on an assembled catalog rerun.
    manifest = json.loads((CORE / "manifest.json").read_text())
    declared = {CORE / r["path"] for r in manifest["resources"]}
    for source in sources:
        relative = source.relative_to(legacy_root)
        target = STAGING / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != source_hash:
                raise ValueError(f"staging collision; preserving both files: {source} -> {target}")
        else:
            copy_file(source, target)
    for source in sources:
        if source not in declared:
            source.unlink()


def stage_core_spleen32() -> None:
    """Preserve Spleen 32x64 outside installed trees before repackaging it."""
    source = CORE / "assets/fonts/spleen-32x64.bdf"
    if not source.is_file():
        return
    target = STAGING / "spleen/2.2.0/spleen-32x64.bdf"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError(f"staging collision; preserving both files: {source} -> {target}")
    if not target.exists():
        copy_file(source, target)


def preserve_ark_archival_inventory() -> None:
    source = FAMILIES / "ark-pixel.json"
    if not source.is_file():
        return
    archival = ROOT / ".source-cache/archival/ark-pixel/full-inventory.json"
    archival.parent.mkdir(parents=True, exist_ok=True)
    if archival.exists():
        existing = json.loads(archival.read_text())
    else:
        existing = json.loads(source.read_text())
        copy_file(source, archival)
    distributed = dict(existing)
    distributed["fonts"] = [row for row in existing.get("fonts", []) if not row.get("deprecated")]
    distributed["counts"] = dict(existing.get("counts", {}))
    distributed["counts"]["fonts"] = len(distributed["fonts"])
    distributed["counts"]["font_bytes"] = sum(row["bytes"] for row in distributed["fonts"])
    distributed["license_notices"] = [row for row in existing.get("license_notices", []) if "deprecated-16px" not in row.get("path", "")]
    distributed["license_path"] = distributed["license_notices"][0]["path"]
    distributed["expected"] = {"sizes_px": [10, 12], "styles": ["mono", "proportional"], "regions_per_size_style": 7}
    distributed["size_style_region_counts"] = {key: value for key, value in existing.get("size_style_region_counts", {}).items() if key in {"10", "12"}}
    source.write_text(json.dumps(distributed, indent=2, ensure_ascii=False) + "\n")


def migrate_source_inventories() -> None:
    """Move canonical import inventories out of installable package trees safely."""
    legacy = CORE / "inventory"
    family_dir = legacy / "families"
    for source in sorted(family_dir.glob("*.json")) if family_dir.exists() else []:
        target = FAMILIES / source.name
        if target.exists() and hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
            raise ValueError(f"source inventory cache collision; preserving both: {source} -> {target}")
        copy_file(source, target)
    legacy_files = [legacy / name for name in ("families.json", "fonts.json", "fonts.csv", "fonts-todo.json")]
    archive = ROOT / ".source-cache/inventory/legacy-core"
    for source in legacy_files:
        if not source.is_file():
            continue
        target = archive / source.name
        if target.exists() and source.name in {"families.json", "fonts.json"}:
            # These are assembler outputs after the first migration. The cached
            # pre-migration originals remain authoritative and untouched.
            continue
        if target.exists() and hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
            raise ValueError(f"legacy inventory cache collision; preserving both: {source} -> {target}")
        copy_file(source, target)
    if family_dir.exists():
        for source in family_dir.glob("*.json"):
            source.unlink()
        family_dir.rmdir()
    for source in legacy_files:
        source.unlink(missing_ok=True)


def make_resource(row: dict, family: str, package: Path, package_id: str) -> tuple[dict, dict]:
    source_rel = relative_source(row)
    source = safe_source(source_rel)
    expected = row.get("sha256", row.get("source_file_sha256"))
    if expected and hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise ValueError(f"original BDF hash mismatch: {source}")
    # Keep family-internal relative paths; package-qualified references are
    # unambiguous because resource IDs are scoped by manifest package ID.
    relpath = PurePosixPath(source_rel).relative_to(".source-cache/fonts")
    target_rel = PurePosixPath("fonts") / relpath
    target = package / Path(str(target_rel))
    digest = copy_file(source, target)
    rid = font_id(row, family)
    resource = {
        "id": rid,
        "kind": "font",
        "path": str(target_rel),
        "license": load(family).get("license", "SEE-LICENSE") if isinstance(load(family).get("license"), str) else "SEE-LICENSE",
        "provenance": f"{load(family).get('family', family)}; original BDF SHA-256 {digest}",
    }
    metadata = dict(row)
    metadata["id"] = rid
    metadata["path"] = str(target_rel)
    metadata["sha256"] = digest
    metadata["bytes"] = target.stat().st_size
    return resource, metadata


def assemble_package(package_id: str, families: list[str], *, core: bool = False) -> dict:
    package = ROOT / "packages" / package_id
    package.mkdir(parents=True, exist_ok=True)
    resources: list[dict] = []
    metadata_fonts: list[dict] = []
    license_files: list[str] = []
    inventory: dict = {"package": package_id, "families": {}}

    if core:
        existing = json.loads((CORE / "manifest.json").read_text())
        resources.extend(r for r in existing["resources"] if r["id"] in {
            "tom-thumb-4x6", "spleen-5x8", "unifont-16.0.04"})
        base_ids = {r["id"] for r in resources}
        base_metadata = {r["id"]: r for r in existing.get("metadata", {}).get("fonts", []) if r["id"] in base_ids}
        metadata_fonts.extend(base_metadata.values())
        license_files.extend(["licenses/TomThumb-LICENSE.txt", "licenses/Spleen-LICENSE.txt", "licenses/UNIFONT-COPYING.txt"])

    selected: list[tuple[str, dict]] = []
    for family in families:
        data = package_family_data(family)
        rows = font_rows(family)
        if core and family == "fusion-pixel":
            rows = [r for r in rows if PurePosixPath(r["filename"]).name in FUSION_CORE]
        for row in rows:
            row = dict(row)
            row["family"] = family
            selected.append((family, row))
        if core and family == "fusion-pixel":
            data["fonts"] = [r for r in data.get("fonts", []) if PurePosixPath(r.get("filename", "")).name in FUSION_CORE]
            if isinstance(data.get("counts"), dict):
                data["counts"]["fonts"] = len(data["fonts"])
                data["counts"]["font_bytes"] = sum(r.get("bytes", 0) for r in data["fonts"])
        inventory["families"][family] = data
        for src, declared in license_sources(family):
            source = safe_source(src)
            # Keep family isolation and nested upstream notice paths; distinct
            # notices often share a basename (notably Ark Pixel's two OFLs).
            license_rel = PurePosixPath(declared)
            copy_file(source, package / Path(str(license_rel)))
            license_files.append(str(license_rel))

    for family, row in selected:
        resource, metadata = make_resource(row, family, package, package_id)
        resources.append(resource)
        metadata_fonts.append(metadata)

    # Preserve actual widgets/runtime contract only in core. Font packages are
    # resource-only and have no invented dependency or entry point.
    if core:
        manifest = existing
        core_font_ids = {r["id"] for r in resources}
        for widget in manifest.get("widgets", []):
            schema = widget.get("config_schema", {}).get("properties", {}).get("font", {})
            if "enum" in schema:
                schema["enum"] = [font_id for font_id in schema["enum"] if font_id in core_font_ids]
        manifest["resources"] = resources
        manifest["license_files"] = list(dict.fromkeys(license_files))
        manifest["metadata"] = {**manifest.get("metadata", {}), "fonts": metadata_fonts}
    else:
        manifest = {
            "id": package_id,
            "version": "0.1.0",
            "sdk": {"min": "1.0.0", "max": "1.0.0"},
            "license": "SEE LICENSE FILES",
            "provenance": {"source": "Original BDF assets and family provenance in inventory/families"},
            "license_files": list(dict.fromkeys(license_files)),
            "resources": resources,
            "metadata": {"fonts": metadata_fonts},
        }
    # Runtime manifests are bounded to 64 KiB. Full native glyph coverage and
    # archive provenance stay in inventory, rather than being duplicated here.
    manifest["metadata"]["fonts"] = [
        {key: value for key, value in row.items() if key in {
            "id", "path", "sha256", "bytes", "version", "source_url",
            "license", "source_license_url"}}
        for row in metadata_fonts
    ]
    encoded = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError(f"{package_id} manifest exceeds runtime 64 KiB bound")
    (package / "manifest.json").write_text(encoded)
    invdir = package / "inventory"
    invdir.mkdir(exist_ok=True)
    (invdir / "families.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n")
    if core:
        (invdir / "fonts.json").write_text(json.dumps(metadata_fonts, indent=2, ensure_ascii=False) + "\n")
    count = len(resources)
    byte_total = sum((package / r["path"]).stat().st_size for r in resources)
    return {"package": package_id, "fonts": count, "font_bytes": byte_total,
            "max_font_bytes": max(((package / r["path"]).stat().st_size for r in resources), default=0),
            "license_files": len(manifest["license_files"]),
            "license_bytes": sum((package / f).stat().st_size for f in manifest["license_files"])}


def validate_package(package_id: str, expected_count: int, expected_bytes: int) -> None:
    package = ROOT / "packages" / package_id
    manifest = json.loads((package / "manifest.json").read_text())
    declared = {str((package / r["path"]).resolve()) for r in manifest["resources"]
                if r.get("kind") == "font" and str(r.get("path", "")).lower().endswith(".bdf")}
    actual = {str(path.resolve()) for path in package.rglob("*.bdf")}
    extra, missing = actual - declared, declared - actual
    if extra or missing:
        raise ValueError(f"{package_id} BDF inventory mismatch; undeclared={sorted(extra)}, missing={sorted(missing)}")
    total = sum(Path(path).stat().st_size for path in actual)
    if len(actual) != expected_count or total != expected_bytes:
        raise ValueError(f"{package_id} actual BDF total is {len(actual)} files/{total} bytes; expected {expected_count}/{expected_bytes}")


def archive_unused_notices() -> None:
    for name in ("core", "fonts-extra", "font-fusion-pixel", "font-ark-pixel"):
        package = ROOT / "packages" / name
        manifest = json.loads((package / "manifest.json").read_text())
        declared = {package / p for p in manifest["license_files"]}
        extras = [p for p in (package / "licenses").rglob("*") if p.is_file() and p not in declared]
        for p in extras:
            target = NOTICES / p.relative_to(package)
            if target.exists() and hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(p.read_bytes()).digest():
                raise ValueError(f"notice staging collision: {p}")
            copy_file(p, target)
        for p in extras:
            p.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.root.resolve() != ROOT.resolve():
        raise SystemExit("--root must be this checkout; run the script in its own repository")
    migrate_legacy_staging()
    migrate_source_inventories()
    stage_core_spleen32()
    preserve_ark_archival_inventory()
    legacy_fonts = ROOT / "packages/fonts"
    extra_fonts = ROOT / "packages/fonts-extra"
    if legacy_fonts.exists() and not extra_fonts.exists():
        os.replace(legacy_fonts, extra_fonts)
    result = [
        assemble_package("core", ["fusion-pixel"], core=True),
        assemble_package("fonts-extra", sorted(CORE_FAMILIES)),
        assemble_package("font-fusion-pixel", ["fusion-pixel"]),
        assemble_package("font-ark-pixel", ["ark-pixel"]),
    ]
    # Retire stale BDF copies only after every new manifest and replacement
    # resource has been written; all originals remain in ignored source-cache.
    for package_id in ("core", "fonts-extra", "font-fusion-pixel", "font-ark-pixel"):
        package = ROOT / "packages" / package_id
        manifest = json.loads((package / "manifest.json").read_text())
        declared = {str((package / r["path"]).resolve()) for r in manifest["resources"] if r.get("kind") == "font"}
        for path in package.rglob("*.bdf"):
            if str(path.resolve()) not in declared:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                preserved = [p for p in STAGING.rglob(path.name) if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest]
                if not preserved:
                    raise ValueError(f"refusing to remove unpreserved BDF {path} ({digest})")
                path.unlink()
    # Count assertions are checked by the independent catalog validator; derive
    # exact totals from the source inventories rather than preserving old pins.
    expected_counts = {"core": 6, "fonts-extra": 72, "font-fusion-pixel": 42, "font-ark-pixel": 28}
    for package_id in ("core", "fonts-extra", "font-fusion-pixel", "font-ark-pixel"):
        package = ROOT / "packages" / package_id
        manifest = json.loads((package / "manifest.json").read_text())
        validate_package(package_id, expected_counts[package_id], sum((package / r["path"]).stat().st_size for r in manifest["resources"]))
    archive_unused_notices()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
