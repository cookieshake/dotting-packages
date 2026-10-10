#!/usr/bin/env python3
"""Import pinned, unmodified Galmuri BDF files and their upstream OFL license."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "bdb86ae89466a361eb8df861222736b81ff975ef"
VERSION = "2.40.4"
BASE_URL = f"https://raw.githubusercontent.com/quiple/galmuri/{COMMIT}/dist"
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 64
HTTP_TIMEOUT = 30
LICENSE_SHA256 = "86a3ee9495f942f0243f18c103da9faca27adb88142613edb8bb852e56c892c1"
LICENSE_BYTES = 4360

FILENAMES = (
    "Galmuri7.bdf", "Galmuri9.bdf", "Galmuri11.bdf", "Galmuri14.bdf",
    "Galmuri11-Bold.bdf", "Galmuri11-Condensed.bdf",
    "GalmuriMono7.bdf", "GalmuriMono9.bdf", "GalmuriMono11.bdf",
)


def fetch(filename: str) -> tuple[str, bytes]:
    url = f"{BASE_URL}/{filename}"
    request = urllib.request.Request(url, headers={"User-Agent": "dotting-galmuri-importer/1.0"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        data = response.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError(f"{filename} exceeds {MAX_FILE} byte per-file limit")
    return filename, data


def metadata(filename: str, data: bytes) -> dict[str, object]:
    text = data.decode("utf-8")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("STARTFONT ") or lines[-1] != "ENDFONT":
        raise ValueError(f"invalid BDF framing: {filename}")

    def one(pattern: str, label: str) -> str:
        values = re.findall(pattern, text, re.MULTILINE)
        if len(values) != 1:
            raise ValueError(f"expected one {label} in {filename}, found {len(values)}")
        return values[0]

    font_name = one(r"^FONT\s+(.+)$", "FONT")
    bbox = one(r"^FONTBOUNDINGBOX\s+(.+)$", "FONTBOUNDINGBOX")
    ascent = int(one(r"^FONT_ASCENT\s+(\d+)\s*$", "FONT_ASCENT"))
    descent = int(one(r"^FONT_DESCENT\s+(\d+)\s*$", "FONT_DESCENT"))
    count = int(one(r"^CHARS\s+(\d+)\s*$", "CHARS"))
    encodings = [int(v) for v in re.findall(r"^ENCODING\s+(-?\d+)(?:\s+-?\d+)?\s*$", text, re.MULTILINE) if int(v) >= 0]
    startchars = len(re.findall(r"^STARTCHAR\s+", text, re.MULTILINE))
    if count != startchars or count != len(re.findall(r"^ENDCHAR\s*$", text, re.MULTILINE)):
        raise ValueError(f"glyph record count mismatch in {filename}: CHARS={count}, records={startchars}")
    if len(encodings) != count:
        raise ValueError(f"encoding count mismatch in {filename}: CHARS={count}, encodings={len(encodings)}")
    codes = sorted(set(encodings))
    ranges: list[str] = []
    if codes:
        low = high = codes[0]
        for code in codes[1:]:
            if code == high + 1:
                high = code
            else:
                ranges.append(f"U+{low:04X}" if low == high else f"U+{low:04X}-U+{high:04X}")
                low = high = code
        ranges.append(f"U+{low:04X}" if low == high else f"U+{low:04X}-U+{high:04X}")
    return {
        "filename": filename, "font_name": font_name, "bounding_box": bbox,
        "ascent": ascent, "descent": descent, "glyph_count": count,
        "coverage_codepoint_count": len(codes), "coverage_ranges": ranges,
        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.resolve().is_relative_to(ROOT) or path.is_symlink():
        raise ValueError(f"unsafe destination: {path}")
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


def main() -> None:
    if len(FILENAMES) + 1 > MAX_FILES:
        raise ValueError("configured file count exceeds limit")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = dict(pool.map(fetch, FILENAMES))
    license_name, license_data = fetch("LICENSE.txt")
    del license_name
    if len(license_data) != LICENSE_BYTES or hashlib.sha256(license_data).hexdigest() != LICENSE_SHA256:
        raise ValueError("upstream Galmuri license size or SHA-256 mismatch")
    total = sum(map(len, downloaded.values())) + len(license_data)
    if total > MAX_TOTAL:
        raise ValueError(f"aggregate download exceeds {MAX_TOTAL} bytes")

    records = []
    for filename in FILENAMES:
        data = downloaded[filename]
        record = metadata(filename, data)
        stem = Path(filename).stem.removeprefix("Galmuri")
        if stem.startswith("Mono"):
            size, style = stem.removeprefix("Mono"), "mono"
        elif stem.endswith("-Bold"):
            size, style = stem.removesuffix("-Bold"), "bold"
        elif stem.endswith("-Condensed"):
            size, style = stem.removesuffix("-Condensed"), "condensed"
        else:
            size, style = stem, "regular"
        record.update({
            "id": f"fonts-galmuri-{size}-{style}",
            "path": f".source-cache/fonts/galmuri/{filename}",
            "version": VERSION,
            "source_url": f"{BASE_URL}/{filename}",
            "source_commit": COMMIT,
            "license": "OFL-1.1",
            "license_path": "licenses/galmuri/LICENSE.txt",
        })
        records.append(record)

    for filename, data in downloaded.items():
        write_atomic(ROOT / ".source-cache/fonts/galmuri" / filename, data)
    write_atomic(ROOT / "packages/core/licenses/galmuri/LICENSE.txt", license_data)
    inventory = {
        "family": "Galmuri", "version": VERSION, "license": "OFL-1.1",
        "license_path": "licenses/galmuri/LICENSE.txt",
        "license_source_url": f"{BASE_URL}/LICENSE.txt",
        "license_sha256": LICENSE_SHA256, "license_bytes": len(license_data),
        "source_repository": "https://github.com/quiple/galmuri",
        "source_commit": COMMIT, "fonts": records,
    }
    write_atomic(ROOT / ".source-cache/inventory/families/galmuri.json", (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for row in records:
        print(f"{row['path']}: {row['bytes']} bytes sha256={row['sha256']} glyphs={row['glyph_count']} bbox={row['bounding_box']}")
    print(f"licenses/galmuri/LICENSE.txt: {len(license_data)} bytes sha256={LICENSE_SHA256}")
    print(f"total: {total} bytes; files: {len(records) + 1}")


if __name__ == "__main__":
    main()
