#!/usr/bin/env python3
"""Fetch and inventory pinned native BDFs; never convert font formats."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING_ROOT = ROOT / ".source-cache/fonts"
MAX_DOWNLOAD = 128 * 1024
RUNTIME_ROOT = ROOT.parent / "dotting"
SOURCES = (
    {
        "id": "tom-thumb-4x6",
        "path": "packages/core/assets/fonts/tom-thumb-4x6.bdf",
        "url": "https://robey.lag.net/downloads/tom-thumb.bdf",
        "version": "upstream 2010-01-23",
        "sha256": "d2c8c15de5ca83fcaef7cadc06d8db578c082507fa3da8a8f698d026fdda2b14",
        "license": "MIT",
        "license_url": "https://robey.lag.net/2010/01/23/tiny-monospace-font.html",
        "bbox": "3 6 0 -1",
        "ascent": 5,
        "descent": 1,
        "glyphs": 203,
    },
    {
        "id": "spleen-5x8",
        "path": "packages/core/assets/fonts/spleen-5x8.bdf",
        "url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/spleen-5x8.bdf",
        "version": "2.2.0",
        "sha256": "40488184d075d0c752cdd239b441c5ece51e50b353156f2496c756c384ab01cb",
        "license": "BSD-2-Clause",
        "license_url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/LICENSE",
        "bbox": "5 8 0 -1",
        "ascent": 7,
        "descent": 1,
        "glyphs": 472,
    },
    {
        "id": "spleen-32x64",
        "path": ".source-cache/fonts/spleen/2.2.0/spleen-32x64.bdf",
        "url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/spleen-32x64.bdf",
        "version": "2.2.0",
        "sha256": "46897e4c11aec89547805c329b1e8fb572f7580ccb2f7217f5b82aff3b035a71",
        "license": "BSD-2-Clause",
        "license_url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/LICENSE",
        "bbox": "32 64 0 -12",
        "ascent": 52,
        "descent": 12,
        "glyphs": 978,
    },
    {
        "id": "fonts-spleen-6x12-latin",
        "path": ".source-cache/fonts/spleen/2.2.0/spleen-6x12.bdf",
        "url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/spleen-6x12.bdf",
        "version": "2.2.0",
        "sha256": "fc0743d164690f99b7e2e1b9d503180e4c719a9831ae03fd8f6da18c857dee27",
        "license": "BSD-2-Clause",
        "license_url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/LICENSE",
        "bbox": "6 12 0 -3",
        "ascent": 9,
        "descent": 3,
        "glyphs": 548,
    },
    {
        "id": "fonts-spleen-8x16-latin",
        "path": ".source-cache/fonts/spleen/2.2.0/spleen-8x16.bdf",
        "url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/spleen-8x16.bdf",
        "version": "2.2.0",
        "sha256": "4a3d97ee61a8c86a7525d8c723cb8a14081f395cd2feb4227ba5e3baf0629bae",
        "license": "BSD-2-Clause",
        "license_url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/LICENSE",
        "bbox": "8 16 0 -4",
        "ascent": 12,
        "descent": 4,
        "glyphs": 978,
    },
    {
        "id": "fonts-spleen-12x24-latin",
        "path": ".source-cache/fonts/spleen/2.2.0/spleen-12x24.bdf",
        "url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/spleen-12x24.bdf",
        "version": "2.2.0",
        "sha256": "181989f2c5f47b34c2474cd424b387b0002aa77fc970e59ee31e16733af24687",
        "license": "BSD-2-Clause",
        "license_url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/LICENSE",
        "bbox": "12 24 0 -5",
        "ascent": 19,
        "descent": 5,
        "glyphs": 926,
    },
    {
        "id": "fonts-spleen-16x32-latin",
        "path": ".source-cache/fonts/spleen/2.2.0/spleen-16x32.bdf",
        "url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/spleen-16x32.bdf",
        "version": "2.2.0",
        "sha256": "f6db2549d46c5699ceca7ccc26e747c8bdad3af76239f86aae6ed995bd2f3d37",
        "license": "BSD-2-Clause",
        "license_url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/LICENSE",
        "bbox": "16 32 0 -6",
        "ascent": 26,
        "descent": 6,
        "glyphs": 978,
    },
    {
        "id": "unifont-16.0.04",
        "path": "packages/core/assets/fonts/unifont-16.0.04.bdf",
        "url": "https://unifoundry.com/pub/unifont/unifont-16.0.04/font-builds/unifont-16.0.04.bdf.gz",
        "version": "16.0.04",
        "sha256": "40343cd7e33df7351c5a448497473697f82865e766c3ebb059f3ed6ade765587",
        "license": "GPL-2.0-or-later WITH Font-exception-2.0",
        "license_url": "https://unifoundry.com/pub/unifont/unifont-16.0.04/unifont-16.0.04.tar.gz (COPYING)",
        "bbox": "16 16 0 -2",
        "ascent": 14,
        "descent": 2,
        "glyphs": 57086,
        "local_source": "assets/fonts/unifont-16.0.04.bdf",
    },
)
SPLEEN_LICENSE = {
    "path": "packages/core/licenses/Spleen-LICENSE.txt",
    "url": "https://raw.githubusercontent.com/fcambus/spleen/2.2.0/LICENSE",
    "sha256": "f33fe8679d5b2abecc4f1313ce6c6bfa58262964de5f7bca146596a7318047af",
}
TOM_LICENSE = ROOT / "packages/core/licenses/TomThumb-LICENSE.txt"
UNIFONT_LICENSE = {
    "path": "packages/core/licenses/UNIFONT-COPYING.txt",
    "source": "assets/fonts/UNIFONT-COPYING.txt",
    "sha256": "5a65797606332f2f63057fd81ea0ff35f74043583515bf900ee9b42efa8176a6",
}


def safe_output(relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise ValueError(f"unsafe output path: {relative}")
    target = ROOT.joinpath(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = target.parent.resolve()
    if not resolved_parent.is_relative_to(ROOT):
        raise ValueError(f"output escapes repository: {relative}")
    if target.is_symlink():
        raise ValueError(f"refusing symlink output: {relative}")
    return target


def download(url: str, expected_sha256: str, maximum: int = MAX_DOWNLOAD) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "dotting-font-importer/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"download exceeds {maximum} byte bound: {url}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for {url}: expected {expected_sha256}, got {actual}")
    return data


def atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def header(data: bytes, spec: dict[str, object]) -> dict[str, object]:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError(f"BDF is not ASCII for {spec['id']}") from error
    lines = text.splitlines()
    if not lines or lines[0] != "STARTFONT 2.1":
        raise ValueError(f"unexpected BDF format for {spec['id']}")
    fields: dict[str, str] = {}
    for key in ("FONT ", "FONTBOUNDINGBOX ", "FONT_ASCENT ", "FONT_DESCENT ", "CHARS "):
        matches = [line[len(key) :] for line in lines if line.startswith(key)]
        if len(matches) != 1:
            raise ValueError(f"expected one {key.strip()} header for {spec['id']}")
        fields[key.strip()] = matches[0]
    if fields["FONTBOUNDINGBOX"] != spec["bbox"]:
        raise ValueError(f"BDF bounding-box mismatch for {spec['id']}")
    if int(fields["FONT_ASCENT"]) != spec["ascent"] or int(fields["FONT_DESCENT"]) != spec["descent"]:
        raise ValueError(f"BDF ascent/descent mismatch for {spec['id']}")
    count = int(fields["CHARS"])
    if count != spec["glyphs"] or text.count("\nSTARTCHAR ") != count:
        raise ValueError(f"BDF glyph count mismatch for {spec['id']}")
    return {
        "font_name": fields["FONT"],
        "bounding_box": fields["FONTBOUNDINGBOX"],
        "ascent": int(fields["FONT_ASCENT"]),
        "descent": int(fields["FONT_DESCENT"]),
        "glyph_count": count,
    }


def main() -> None:
    tom_notice = TOM_LICENSE.read_bytes()
    if b"MIT License" not in tom_notice or b"Robey Pointer" not in tom_notice or b"SOFTWARE." not in tom_notice:
        raise ValueError("Tom Thumb license notice is missing required attribution/license text")

    inventory = []
    for spec in SOURCES:
        if "local_source" in spec:
            source = RUNTIME_ROOT / str(spec["local_source"])
            with source.open("rb") as input_file:
                data = input_file.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError(f"local font exceeds 16 MiB bound: {source}")
            if hashlib.sha256(data).hexdigest() != spec["sha256"]:
                raise ValueError(f"SHA-256 mismatch for local read-only font: {source}")
        else:
            data = download(str(spec["url"]), str(spec["sha256"]), 2 * 1024 * 1024)
        metrics = header(data, spec)
        target = safe_output(str(spec["path"]))
        atomic_write(target, data)
        inventory.append(
            {
                "id": spec["id"],
                "path": ("fonts/" + str(Path(str(spec["path"])).relative_to(".source-cache/fonts"))
                         if str(spec["path"]).startswith(".source-cache/fonts/")
                         else str(Path(str(spec["path"])).relative_to("packages/core"))),
                "version": spec["version"],
                "source_url": spec["url"],
                "license_url": spec["license_url"],
                "license": spec["license"],
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                **metrics,
            }
        )

    spleen_license = download(SPLEEN_LICENSE["url"], SPLEEN_LICENSE["sha256"], 16 * 1024)
    atomic_write(safe_output(SPLEEN_LICENSE["path"]), spleen_license)
    unifont_license_path = RUNTIME_ROOT / UNIFONT_LICENSE["source"]
    with unifont_license_path.open("rb") as input_file:
        unifont_license = input_file.read(64 * 1024 + 1)
    if len(unifont_license) > 64 * 1024 or hashlib.sha256(unifont_license).hexdigest() != UNIFONT_LICENSE["sha256"]:
        raise ValueError("Unifont license source hash mismatch")
    if b"GNU font embedding exception" not in unifont_license or b"SIL OPEN FONT LICENSE Version 1.1" not in unifont_license:
        raise ValueError("Unifont license source does not contain the expected font licensing terms")
    atomic_write(safe_output(UNIFONT_LICENSE["path"]), unifont_license)
    inventory_dir = safe_output(".source-cache/inventory/.sentinel").parent
    json_path = inventory_dir / "fonts.json"
    csv_path = inventory_dir / "fonts.csv"
    atomic_write(json_path, (json.dumps(inventory, indent=2, sort_keys=True) + "\n").encode())
    columns = ["id", "path", "version", "source_url", "license_url", "license", "sha256", "bytes", "font_name", "bounding_box", "ascent", "descent", "glyph_count"]
    with io.StringIO(newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(inventory)
        atomic_write(csv_path, output.getvalue().encode())
    (inventory_dir / ".sentinel").unlink(missing_ok=True)
    for row in inventory:
        print(f"{row['id']}: {row['bytes']} bytes sha256={row['sha256']} glyphs={row['glyph_count']} bbox={row['bounding_box']}")
    print(f"inventory: {json_path.relative_to(ROOT)}, {csv_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
