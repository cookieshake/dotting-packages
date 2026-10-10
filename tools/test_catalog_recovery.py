#!/usr/bin/env python3
"""Offline importer replay and physical recovery regression proof.

Replay uses hash-verified original BDF/notice bytes, reconstructed archives and
intercepted writes. It tests real importer routing, not network/archive pins.
"""
import contextlib
import hashlib
import importlib
import io
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import assemble_catalog as catalog
import validate_catalog as validator

ROOT = catalog.ROOT


def snapshot():
    return {str(p.relative_to(ROOT)): validator.sha(p)
            for p in (ROOT / "packages").rglob("*") if p.is_file()}


def source_rows(family):
    data = catalog.load(family)
    rows = data.get("fonts", data.get("files", []))
    originals = []
    for row in rows:
        path = catalog.safe_source(catalog.relative_source(dict(row, family=family)))
        blob = path.read_bytes()
        assert hashlib.sha256(blob).hexdigest() == row.get("sha256", row.get("source_file_sha256"))
        originals.append((row, blob))
    return data, originals


def replay(module, patches):
    writes = []
    output_root = patches.get("ROOT", ROOT)
    def record(path, blob):
        writes.append((Path(path), blob))
    writer = "atomic_write" if module.__name__ == "import_fonts" else "atomic" if module.__name__ == "import_remaining_latin" else "write_atomic"
    with contextlib.ExitStack() as stack:
        for key, value in patches.items():
            stack.enter_context(patch.object(module, key, value))
        stack.enter_context(patch.object(module, writer, record))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        module.main()
        module.main()
    bdfs = [(p, b) for p, b in writes if p.suffix.lower() == ".bdf"]
    allowed_base = {output_root / str(s["path"]) for s in importlib.import_module("import_fonts").SOURCES
                    if str(s["path"]).startswith("packages/")}
    for p, blob in bdfs:
        assert p.is_relative_to(output_root / ".source-cache/fonts") or (module.__name__ == "import_fonts" and p in allowed_base), p
    if module.__name__ == "import_ark_pixel":
        assert all(not p.is_relative_to(output_root / "packages/core/licenses") for p, _ in writes), writes
    assert writes, module.__name__
    return {"importer": module.__name__, "runs": 2, "intercepted_writes": len(writes), "bdf_writes": len(bdfs)}


def importer_replays(work):
    results = []
    for family in ("fusion-pixel", "ark-pixel"):
        module = importlib.import_module("import_" + family.replace("-", "_"))
        data, originals = source_rows(family)
        downloads = {}
        for archive in {r["archive"] for r, _ in originals}:
            path = work / archive
            rows = [(r, b) for r, b in originals if r["archive"] == archive]
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for row, blob in rows:
                    z.writestr(Path(row["filename"]).name, blob)
                for notice in data["license_notices"]:
                    if family == "ark-pixel" and ("deprecated-16px" in notice["path"]) != bool(rows[0][0]["deprecated"]):
                        continue
                    z.writestr(notice["source_member"], catalog.safe_source(notice["path"]).read_bytes())
            downloads[archive] = (archive, path, validator.sha(path), path.stat().st_size)
        results.append(replay(module, {"fetch": lambda name, work, d=downloads: d[name]}))
    module = importlib.import_module("import_fonts")
    by_url = {str(s["url"]): (ROOT / str(s["path"])).read_bytes() for s in module.SOURCES if "local_source" not in s}
    by_url[module.SPLEEN_LICENSE["url"]] = (ROOT / module.SPLEEN_LICENSE["path"]).read_bytes()
    def font_fetch(url, expected, maximum):
        blob = by_url[url]
        assert len(blob) <= maximum and hashlib.sha256(blob).hexdigest() == expected
        return blob
    results.append(replay(module, {"download": font_fetch}))
    module = importlib.import_module("import_galmuri")
    data, originals = source_rows("galmuri")
    blobs = {r["filename"]: b for r, b in originals}
    blobs["LICENSE.txt"] = catalog.safe_source(data["license_path"]).read_bytes()
    results.append(replay(module, {"fetch": lambda name: (name, blobs[name])}))
    module = importlib.import_module("import_remaining_latin")
    candidates, urls = {}, {}
    for family in module.FAMILIES:
        data, originals = source_rows(family)
        candidates[family] = ([(r["upstream_path"], r["source_url"], r["version"], b) if family == "scientifica"
                               else (r["upstream_path"], r["source_url"], r["version"]) for r, b in originals], data["source"])
        urls.update({r["source_url"]: b for r, b in originals})
        urls[data["license"]["source_url"]] = catalog.safe_source(f"licenses/{family}/LICENSE.txt").read_bytes()
    results.append(replay(module, {"ROOT": work / "latin-empty-staging", "family_files": lambda name, spec: candidates[name], "fetch": lambda url, limit: urls[url]}))
    module = importlib.import_module("import_littlelimit")
    families = {k: dict(v) for k, v in module.FAMILIES.items()}
    blobs = {}
    for family, spec in families.items():
        data, originals = source_rows(family)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for row, blob in originals:
                z.writestr(row["filename"], blob)
            z.writestr(data["license_notice_source_member"], catalog.safe_source(data["license_path"]).read_bytes())
        blob = buffer.getvalue()
        spec["archive_sha256"] = hashlib.sha256(blob).hexdigest()
        blobs[spec["base"] + spec["archive"]] = blob
    results.append(replay(module, {"FAMILIES": families, "fetch": lambda url, limit: blobs[url]}))
    return results


def main():
    before = snapshot()
    for _ in range(2):
        subprocess.run(["python3", str(ROOT / "tools/assemble_catalog.py")], check=True, capture_output=True)
        assert snapshot() == before, "assembler changed installed bytes on rerun"
    rejected = []
    for name in validator.EXPECTED:
        path = ROOT / "packages" / name / "recovery-negative-undeclared.bdf"
        assert not path.exists(), path
        try:
            with path.open("xb") as output:
                output.write(b"STARTFONT 2.1\nENDFONT\n")
            try:
                catalog.validate_package(name, *validator.EXPECTED[name][:2])
            except ValueError as error:
                assert "undeclared=" in str(error), error
                rejected.append(name)
            else:
                raise AssertionError(f"undeclared BDF accepted: {name}")
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    validator.validate()
            except AssertionError as error:
                assert "undeclared/missing BDF" in str(error), error
            else:
                raise AssertionError("strict validator accepted undeclared BDF")
        finally:
            path.unlink()
    with tempfile.TemporaryDirectory(prefix="replay-", dir=ROOT / ".source-cache") as temporary:
        results = importer_replays(Path(temporary))
    assert snapshot() == before, "offline importer replay altered installed content"
    paths = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "packages").rglob("*.bdf"))
    proc = subprocess.run(["git", "check-attr", "-z", "--stdin", "filter"], input="\0".join(paths).encode() + b"\0", cwd=ROOT, check=True, capture_output=True)
    parts = proc.stdout.decode().split("\0")[:-1]
    filters = dict((parts[i], parts[i + 2]) for i in range(0, len(parts), 3))
    tom = "packages/core/assets/fonts/tom-thumb-4x6.bdf"
    assert len(filters) == 165
    assert filters[tom] == "unspecified", filters[tom]
    assert all(value == "lfs" for path, value in filters.items() if path != tom), filters
    with contextlib.redirect_stdout(io.StringIO()) as output:
        validator.validate()
    report = {"assembler_identical_reruns": 2, "negative_undeclared_bdf_rejections": rejected,
              "importer_replays": results, "lfs": {"bdf_entries": 165, "lfs_entries": 164, "exception": tom},
              "validation": output.getvalue().splitlines(), "installed_hashes": before,
              "scope": "offline original-byte importer routing replay; no network downloads or remote LFS object verification"}
    # Preserve earlier proof artifacts on every rerun.
    with tempfile.NamedTemporaryFile(mode="w", prefix="recovery-validation-", suffix=".json",
                                     dir=ROOT / ".source-cache", delete=False) as output:
        target = Path(output.name)
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "installed_hashes"}, indent=2))
    print(f"artifact: {target}")


if __name__ == "__main__":
    main()
