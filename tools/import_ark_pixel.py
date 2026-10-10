#!/usr/bin/env python3
"""Import pinned, official Ark Pixel BDF release archives unchanged."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2026.09.25"
REPOSITORY = "https://github.com/TakWolf/ark-pixel-font"
RELEASE_URL = f"{REPOSITORY}/releases/tag/{VERSION}"
BASE_URL = f"{REPOSITORY}/releases/download/{VERSION}"
USER_AGENT = "dotting-ark-pixel-importer/1.0"
HTTP_TIMEOUT = 60
MAX_ARCHIVE = 96 * 1024 * 1024
MAX_ARCHIVE_TOTAL = 512 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_EXTRACTED_TOTAL = 512 * 1024 * 1024
MAX_FILES = 128
MAX_MEMBERS_PER_ARCHIVE = 128
MAX_RATIO = 250

# Exact assets and SHA-256 values captured from the official release API/assets.
ASSET_HASHES = {
    "ark-pixel-font-10px-monospaced-bdf-v2026.09.25.zip": "98028a02bce10c77ddaab435d68be5791382106caa384aaed3f27bfec27e8b59",
    "ark-pixel-font-10px-proportional-bdf-v2026.09.25.zip": "b122a98a304da1ad1c91a303f7bcad92262f6f391b89fa8aeea804d654070314",
    "ark-pixel-font-12px-monospaced-bdf-v2026.09.25.zip": "0caf9f3e14ef06aaaba10b856fc614357d857b44c6d4d210c1ff24023e4b72fa",
    "ark-pixel-font-12px-proportional-bdf-v2026.09.25.zip": "d34b2c84db223a21b4be08943fed156accb311177aaed04c403459ae4a58ebef",
    "ark-pixel-font-16px-monospaced-bdf-v2023.08.24.zip": "c8614876f362660589b6ea002b7ed835f7aaff4aa40556fa6ee8c32c83a605ea",
    "ark-pixel-font-16px-proportional-bdf-v2023.08.24.zip": "64c5feab290a7001918072ee196820b4d5245f1954070790d27e895efb3490ca",
}


def fetch(name: str, work: Path) -> tuple[str, Path, str, int]:
    version_match = re.search(r"-v([0-9.]+)\.zip$", name)
    if version_match is None:
        raise ValueError(f"unrecognized release asset name: {name}")
    request = urllib.request.Request(f"{REPOSITORY}/releases/download/{version_match.group(1)}/{name}", headers={"User-Agent": USER_AGENT})
    path, digest, total = work / name, hashlib.sha256(), 0
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response, path.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE:
                raise ValueError(f"archive exceeds per-archive limit: {name}")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != ASSET_HASHES[name]:
        raise ValueError(f"archive SHA-256 mismatch: {name}")
    return name, path, digest.hexdigest(), total


def safe_member(info: zipfile.ZipInfo) -> PurePosixPath | None:
    name = info.filename
    if "\\" in name or "\x00" in name:
        raise ValueError(f"invalid ZIP member path: {name!r}")
    path = PurePosixPath(name)
    mode = info.external_attr >> 16
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe ZIP member path: {name!r}")
    if (mode & 0o170000) == 0o120000:
        raise ValueError(f"symlink ZIP member rejected: {name!r}")
    if info.flag_bits & 1:
        raise ValueError(f"encrypted ZIP member rejected: {name!r}")
    if info.is_dir():
        return None
    if info.file_size > MAX_MEMBER:
        raise ValueError(f"ZIP member too large: {name!r}")
    if info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_RATIO):
        raise ValueError(f"ZIP compression ratio exceeds limit: {name!r}")
    return path


def bdf_metadata(data: bytes, archive: str, member: str, filename: str) -> dict[str, object]:
    text = data.decode("utf-8-sig")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("STARTFONT ") or lines[-1] != "ENDFONT":
        raise ValueError(f"invalid BDF framing: {archive}:{member}")

    def one(pattern: str, label: str) -> str:
        found = re.findall(pattern, text, re.MULTILINE)
        if len(found) != 1:
            raise ValueError(f"expected one {label} in {archive}:{member}, found {len(found)}")
        return found[0]

    font_name = one(r"^FONT\s+(.+)$", "FONT")
    bbx = [int(value) for value in one(r"^FONTBOUNDINGBOX\s+(.+)$", "FONTBOUNDINGBOX").split()]
    if len(bbx) != 4:
        raise ValueError(f"invalid FONTBOUNDINGBOX in {archive}:{member}")
    glyph_count = int(one(r"^CHARS\s+(\d+)\s*$", "CHARS"))
    matches = list(re.finditer(r"^STARTCHAR\s+(.+?)\s*\n(.*?)^ENDCHAR\s*$", text, re.MULTILINE | re.DOTALL))
    if glyph_count != len(matches) or len(re.findall(r"^ENDCHAR\s*$", text, re.MULTILINE)) != glyph_count:
        raise ValueError(f"glyph record count mismatch in {archive}:{member}")
    glyphs = []
    for match in matches:
        enc = re.search(r"^ENCODING\s+(-?\d+)(?:\s+-?\d+)?\s*$", match.group(2), re.MULTILINE)
        width = re.search(r"^DWIDTH\s+(-?\d+)\s+(-?\d+)\s*$", match.group(2), re.MULTILINE)
        if enc is None or width is None:
            raise ValueError(f"missing ENCODING or DWIDTH in {archive}:{member} glyph {match.group(1)}")
        glyphs.append((int(enc.group(1)), (int(width.group(1)), int(width.group(2)))))
    codes = sorted({code for code, _ in glyphs if code >= 0})
    ranges = []
    if codes:
        lo = hi = codes[0]
        for code in codes[1:]:
            if code == hi + 1:
                hi = code
            else:
                ranges.append(f"U+{lo:04X}" if lo == hi else f"U+{lo:04X}-U+{hi:04X}")
                lo = hi = code
        ranges.append(f"U+{lo:04X}" if lo == hi else f"U+{lo:04X}-U+{hi:04X}")
    digest = hashlib.sha256(data).hexdigest()
    return {
        "filename": filename, "source_member": member, "font_name": font_name,
        "bbx": bbx, "dwidth_values": [list(v) for v in sorted({width for _, width in glyphs})],
        "glyph_count": glyph_count, "coverage_codepoint_count": len(codes),
        "coverage_ranges": ranges, "bytes": len(data), "sha256": digest,
        "source_file_sha256": digest,
    }


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = path.parent.resolve()
    if not parent.is_relative_to(ROOT) or path.is_symlink():
        raise ValueError(f"unsafe destination: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
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
    with tempfile.TemporaryDirectory(prefix="ark-pixel-") as tmp:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            downloads = list(pool.map(lambda name: fetch(name, Path(tmp)), ASSET_HASHES))
        if sum(size for _, _, _, size in downloads) > MAX_ARCHIVE_TOTAL:
            raise ValueError("aggregate archive downloads exceed limit")

        staged: list[tuple[Path, bytes, dict[str, object]]] = []
        license_data: dict[str, bytes] = {}
        license_source_member: dict[str, str] = {}
        license_version: dict[str, str] = {}
        archives: dict[str, dict[str, object]] = {}
        extracted_total = 0
        files_seen = 0
        seen_destinations: set[str] = set()
        for archive, archive_path, archive_sha, archive_bytes in downloads:
            match = re.fullmatch(r"ark-pixel-font-(10|12|16)px-(monospaced|proportional)-bdf-v(.+)\.zip", archive)
            assert match
            size = int(match.group(1))
            spacing = "mono" if match.group(2) == "monospaced" else "proportional"
            version = match.group(3)
            deprecated = size == 16
            license_key = "deprecated_16px" if deprecated else "current"
            with zipfile.ZipFile(archive_path) as zf:
                infos = zf.infolist()
                if len(infos) > MAX_MEMBERS_PER_ARCHIVE:
                    raise ValueError(f"too many ZIP members in {archive}")
                checked = [(info, safe_member(info)) for info in infos]
                archive_regions = []
                archive_license = None
                for info, member in checked:
                    if member is None:
                        continue
                    files_seen += 1
                    if files_seen > MAX_FILES:
                        raise ValueError("aggregate archive member count exceeds limit")
                    if member.name.lower() == "ofl.txt":
                        data = zf.read(info)
                        extracted_total += len(data)
                        archive_license = (member.as_posix(), data)
                        if license_key in license_data and license_data[license_key] != data:
                            raise ValueError("official OFL text differs between release archives")
                        license_data[license_key] = data
                        license_source_member[license_key] = member.as_posix()
                        license_version[license_key] = version
                        continue
                    if member.suffix.lower() != ".bdf":
                        continue
                    data = zf.read(info)
                    extracted_total += len(data)
                    if extracted_total > MAX_EXTRACTED_TOTAL:
                        raise ValueError("aggregate decompressed bytes exceed limit")
                    name_match = re.fullmatch(rf"ark-pixel-{size}px-{match.group(2)}-(.+)\.bdf", member.name)
                    if not name_match:
                        raise ValueError(f"unexpected BDF filename: {member.name}")
                    locale = name_match.group(1)
                    relative = Path(f"{size}px-{spacing}") / member.name
                    key = relative.as_posix()
                    if key in seen_destinations:
                        raise ValueError(f"duplicate destination BDF path: {key}")
                    seen_destinations.add(key)
                    row = bdf_metadata(data, archive, member.as_posix(), key)
                    row.update({
                        "id": f"fonts-ark-pixel-{size}-{spacing}-{locale}" + ("-deprecated16" if deprecated else ""),
                        "path": f".source-cache/fonts/ark-pixel/{key}", "size_px": size,
                        "style": spacing, "region": locale, "locale": locale,
                        "version": version, "source_url": f"{REPOSITORY}/releases/download/{version}/{archive}",
                        "source_release_url": f"{REPOSITORY}/releases/tag/{version}", "archive": archive,
                        "archive_sha256": archive_sha, "archive_bytes": archive_bytes,
                        "deprecated": deprecated,
                        "license": "OFL-1.1", "license_path": "licenses/ark-pixel/deprecated-16px/OFL.txt" if deprecated else "licenses/ark-pixel/OFL.txt",
                    })
                    archive_regions.append(locale)
                    staged.append((ROOT / ".source-cache/fonts/ark-pixel" / relative, data, row))
                if archive_license is None:
                    raise ValueError(f"official OFL notice absent from archive {archive}")
                if not archive_regions:
                    raise ValueError(f"no official BDFs in archive {archive}")
                archives[archive] = {
                    "sha256": archive_sha, "bytes": archive_bytes,
                    "font_count": len(archive_regions), "regions": sorted(archive_regions),
                }
        if extracted_total > MAX_EXTRACTED_TOTAL:
            raise ValueError("aggregate decompressed bytes exceed limit")
        if set(license_data) != {"current", "deprecated_16px"} or any(b"SIL OPEN FONT LICENSE" not in value.upper() for value in license_data.values()):
            raise ValueError("official release OFL text is missing or unrecognizable")
        if len(staged) > MAX_FILES:
            raise ValueError("BDF output count exceeds limit")

        license_rows = []
        license_paths = {}
        for key, data in license_data.items():
            relative = "OFL.txt" if key == "current" else "deprecated-16px/OFL.txt"
            path = f"licenses/ark-pixel/{relative}"
            license_paths[key] = path
            notice = {"filename": relative, "path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "source_member": license_source_member[key], "source_url": f"{REPOSITORY}/releases/tag/{license_version[key]}"}
            license_rows.append(notice)
            write_atomic(ROOT / ".source-cache/notices" / path, data)
        for destination, data, _ in staged:
            write_atomic(destination, data)
        for _, _, row in staged:
            row["license_dependencies"] = [license_paths["deprecated_16px"] if row["deprecated"] else license_paths["current"]]
        sizes = {}
        for size in (10, 12, 16):
            by_style = {}
            for spacing in ("mono", "proportional"):
                selected = [r for _, _, r in staged if r["size_px"] == size and r["style"] == spacing]
                by_style[spacing] = {"expected_region_count": 7, "found_region_count": len(selected), "regions": sorted(r["region"] for r in selected)}
            sizes[str(size)] = by_style
        inventory = {
            "family": "Ark Pixel", "version": VERSION, "license": "OFL-1.1",
            "license_path": license_paths["current"], "license_source_url": RELEASE_URL,
            "license_source_member": license_source_member["current"], "license_sha256": hashlib.sha256(license_data["current"]).hexdigest(),
            "license_bytes": len(license_data["current"]), "license_notices": license_rows,
            "source_repository": REPOSITORY, "source_release_url": RELEASE_URL,
            "expected": {"sizes_px": [10, 12, 16], "styles": ["mono", "proportional"], "regions_per_size_style": 7, "deprecated_16px": {"version": "2023.08.24", "release_url": f"{REPOSITORY}/releases/tag/2023.08.24", "status": "deprecated", "found": True}},
            "counts": {"archives": len(archives), "fonts": len(staged), "font_bytes": sum(len(data) for _, data, _ in staged), "download_bytes": sum(n for _, _, _, n in downloads), "decompressed_bytes": extracted_total},
            "size_style_region_counts": sizes, "archives": archives,
            "fonts": [row for _, _, row in sorted(staged, key=lambda item: item[2]["path"])],
        }
        archival_path = ROOT / ".source-cache/archival/ark-pixel/full-inventory.json"
        write_atomic(archival_path, (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode())
        distributed = dict(inventory)
        distributed["fonts"] = [row for row in inventory["fonts"] if not row["deprecated"]]
        distributed["counts"] = dict(inventory["counts"])
        distributed["counts"]["fonts"] = len(distributed["fonts"])
        distributed["counts"]["font_bytes"] = sum(row["bytes"] for row in distributed["fonts"])
        distributed["license_notices"] = [row for row in license_rows if row["path"] == "licenses/ark-pixel/OFL.txt"]
        distributed["license_path"] = "licenses/ark-pixel/OFL.txt"
        distributed["expected"] = {"sizes_px": [10, 12], "styles": ["mono", "proportional"], "regions_per_size_style": 7}
        distributed["size_style_region_counts"] = {key: value for key, value in sizes.items() if key in {"10", "12"}}
        write_atomic(ROOT / ".source-cache/inventory/families/ark-pixel.json", (json.dumps(distributed, ensure_ascii=False, indent=2) + "\n").encode())
        for row in distributed["fonts"]:
            print(f"{row['path']}: {row['bytes']} bytes sha256={row['sha256']} glyphs={row['glyph_count']} BBX={row['bbx']} DWIDTH={row['dwidth_values']} region={row['region']}")
        for notice in license_rows:
            print(f"license {notice['source_member']} ({notice['path']}): {notice['bytes']} bytes sha256={notice['sha256']}")
        print(f"archives={len(archives)} fonts={len(staged)} font_bytes={inventory['counts']['font_bytes']} download_bytes={inventory['counts']['download_bytes']} decompressed_bytes={extracted_total}; 16px imported only from official archived release 2023.08.24 as deprecated")


if __name__ == "__main__":
    main()
