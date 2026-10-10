#!/usr/bin/env python3
"""Import original BDFs from the pinned official Fusion Pixel release ZIPs."""

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
RELEASE_URL = f"https://github.com/TakWolf/fusion-pixel-font/releases/tag/{VERSION}"
BASE_URL = f"https://github.com/TakWolf/fusion-pixel-font/releases/download/{VERSION}"
USER_AGENT = "dotting-fusion-pixel-importer/1.0"
HTTP_TIMEOUT = 60
MAX_ARCHIVE = 40 * 1024 * 1024
MAX_ARCHIVE_TOTAL = 200 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_EXTRACTED_TOTAL = 1024 * 1024 * 1024
MAX_FILES = 512
MAX_RATIO = 250
MAX_MEMBERS_PER_ARCHIVE = 128

# The release API reported these six BDF archives (and no others are fetched).
ASSETS = tuple(
    f"fusion-pixel-font-{size}px-{style}-bdf-v{VERSION}.zip"
    for size in (8, 10, 12)
    for style in ("monospaced", "proportional")
)


def fetch(name: str, work: Path) -> tuple[str, Path, str, int]:
    url = f"{BASE_URL}/{name}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    path = work / name
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response, path.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE:
                raise ValueError(f"archive exceeds per-archive limit: {name}")
            digest.update(chunk)
            out.write(chunk)
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


def bdf_metadata(data: bytes, archive: str, member: str, dest_name: str) -> dict[str, object]:
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
    bbx_line = one(r"^FONTBOUNDINGBOX\s+(.+)$", "FONTBOUNDINGBOX")
    bbx = [int(v) for v in bbx_line.split()]
    if len(bbx) != 4:
        raise ValueError(f"invalid FONTBOUNDINGBOX in {archive}:{member}")
    chars = int(one(r"^CHARS\s+(\d+)\s*$", "CHARS"))
    records = re.findall(r"^STARTCHAR\s+(.+)$", text, re.MULTILINE)
    ends = len(re.findall(r"^ENDCHAR\s*$", text, re.MULTILINE))
    if chars != len(records) or chars != ends:
        raise ValueError(f"glyph record count mismatch in {archive}:{member}")

    glyphs: list[dict[str, object]] = []
    for match in re.finditer(r"^STARTCHAR\s+(.+?)\s*\n(.*?)^ENDCHAR\s*$", text, re.MULTILINE | re.DOTALL):
        block = match.group(2)
        enc = re.search(r"^ENCODING\s+(-?\d+)(?:\s+-?\d+)?\s*$", block, re.MULTILINE)
        dwidth = re.search(r"^DWIDTH\s+(-?\d+)\s+(-?\d+)\s*$", block, re.MULTILINE)
        if enc is None or dwidth is None:
            raise ValueError(f"missing ENCODING or DWIDTH in {archive}:{member} glyph {match.group(1)}")
        glyphs.append({"encoding": int(enc.group(1)), "dwidth": [int(dwidth.group(1)), int(dwidth.group(2))]})
    if len(glyphs) != chars:
        raise ValueError(f"glyph parse count mismatch in {archive}:{member}")
    codes = sorted({g["encoding"] for g in glyphs if g["encoding"] >= 0})
    ranges: list[str] = []
    if codes:
        lo = hi = codes[0]
        for cp in codes[1:]:
            if cp == hi + 1:
                hi = cp
            else:
                ranges.append(f"U+{lo:04X}" if lo == hi else f"U+{lo:04X}-U+{hi:04X}")
                lo = hi = cp
        ranges.append(f"U+{lo:04X}" if lo == hi else f"U+{lo:04X}-U+{hi:04X}")
    dwidths = sorted({tuple(g["dwidth"]) for g in glyphs})
    return {
        "filename": dest_name,
        "source_member": member,
        "font_name": font_name,
        "bbx": bbx,
        "dwidth_values": [list(v) for v in dwidths],
        "glyph_count": chars,
        "coverage_codepoint_count": len(codes),
        "coverage_ranges": ranges,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
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
    with tempfile.TemporaryDirectory(prefix="fusion-pixel-") as tmp:
        work = Path(tmp)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            downloads = list(pool.map(lambda name: fetch(name, work), ASSETS))
        if sum(size for _, _, _, size in downloads) > MAX_ARCHIVE_TOTAL:
            raise ValueError("aggregate archive downloads exceed limit")

        staged: list[tuple[Path, bytes, dict[str, object]]] = []
        license_candidates: dict[str, bytes] = {}
        archive_licenses: dict[str, list[str]] = {}
        seen_destinations: set[str] = set()
        extracted_total = 0
        files_seen = 0
        for archive, archive_path, archive_sha, archive_bytes in downloads:
            match = re.fullmatch(r"fusion-pixel-font-(\d+)px-(monospaced|proportional)-bdf-v.+\.zip", archive)
            assert match
            size, style = int(match.group(1)), "mono" if match.group(2) == "monospaced" else "proportional"
            with zipfile.ZipFile(archive_path) as zf:
                infos = zf.infolist()
                if len(infos) > MAX_MEMBERS_PER_ARCHIVE:
                    raise ValueError(f"too many ZIP members in {archive}")
                members = [(info, safe_member(info)) for info in infos]
                archive_licenses[archive] = []
                for info, member in members:
                    if member is None:
                        continue
                    files_seen += 1
                    if files_seen > MAX_FILES:
                        raise ValueError("aggregate archive member count exceeds limit")
                    lower = PurePosixPath(member.name).name.lower()
                    if lower in {"ofl.txt", "license.txt", "license", "copyright.txt"}:
                        data = zf.read(info)
                        extracted_total += len(data)
                        if extracted_total > MAX_EXTRACTED_TOTAL:
                            raise ValueError("aggregate decompressed bytes exceed limit")
                        license_key = member.as_posix()
                        if license_key in license_candidates and license_candidates[license_key] != data:
                            raise ValueError(f"license notice differs between archives: {license_key}")
                        license_candidates[license_key] = data
                        archive_licenses[archive].append(license_key)
                    if member.suffix.lower() != ".bdf":
                        continue
                    data = zf.read(info)
                    extracted_total += len(data)
                    if extracted_total > MAX_EXTRACTED_TOTAL:
                        raise ValueError("aggregate decompressed bytes exceed limit")
                    original_name = member.name
                    # Directory dimension separates the six release assets while preserving
                    # the exact original UTF-8 BDF basename and subdirectory layout.
                    rel = Path(f"{size}px-{style}").joinpath(*member.parts)
                    destination = ROOT / ".source-cache/fonts/fusion-pixel" / rel
                    key = rel.as_posix()
                    if key in seen_destinations:
                        raise ValueError(f"duplicate destination BDF path: {key}")
                    seen_destinations.add(key)
                    row = bdf_metadata(data, archive, original_name, key)
                    row.update({
                        "id": f"fonts-fusion-pixel-{size}-{style}-{hashlib.sha256(key.encode()).hexdigest()[:12]}",
                        "path": f".source-cache/fonts/fusion-pixel/{key}",
                        "size_px": size,
                        "style": style,
                        "region": original_name,
                        "version": VERSION,
                        "source_url": f"{BASE_URL}/{archive}",
                        "source_release_url": RELEASE_URL,
                        "archive": archive,
                        "archive_sha256": archive_sha,
                        "archive_bytes": archive_bytes,
                        "archive_license_notices": archive_licenses[archive],
                        "source_file_sha256": hashlib.sha256(data).hexdigest(),
                        "license": "OFL-1.1",
                        "license_path": "licenses/fusion-pixel/OFL.txt",
                    })
                    staged.append((destination, data, row))

        if not staged:
            raise ValueError("no BDF members found in selected release archives")
        if len(staged) > MAX_FILES:
            raise ValueError("BDF output count exceeds limit")
        primary_matches = [(name, data) for name, data in license_candidates.items() if PurePosixPath(name).name.lower() in {"ofl.txt", "license.txt", "license"} and not name.startswith("LICENSES/")]
        if not primary_matches:
            names = ", ".join(sorted(license_candidates)) or "none"
            raise ValueError(f"expected a family license notice in archives, found: {names}")
        license_name, license_data = sorted(primary_matches)[0]
        if not license_data.strip() or b"SIL OPEN FONT LICENSE" not in license_data.upper():
            raise ValueError(f"official archive license is not recognizable as SIL OFL: {license_name}")

        # Check candidate copies from each archive agree byte-for-byte before writing.
        for archive, archive_path, _, _ in downloads:
            with zipfile.ZipFile(archive_path) as zf:
                for name in archive_licenses[archive]:
                    data = license_candidates[name]
                    matches = [i for i in zf.infolist() if PurePosixPath(i.filename).as_posix() == name]
                    if not matches or zf.read(matches[0]) != data:
                        raise ValueError(f"license/provenance mismatch in {archive}: {name}")
                if not any(PurePosixPath(n).name.lower() in {"ofl.txt", "license.txt", "license"} and not n.startswith("LICENSES/") for n in archive_licenses[archive]):
                    raise ValueError(f"family OFL notice absent from archive {archive}")

        for destination, data, _ in staged:
            write_atomic(destination, data)
        write_atomic(ROOT / "packages/core/licenses/fusion-pixel/OFL.txt", license_data)

        license_rows = [{"filename": "OFL.txt", "path": "licenses/fusion-pixel/OFL.txt", "sha256": hashlib.sha256(license_data).hexdigest(), "bytes": len(license_data), "source_member": license_name}]
        for source_member, data in sorted(license_candidates.items()):
            if source_member == license_name:
                continue
            if source_member.startswith("LICENSES/"):
                relative = Path("third-party").joinpath(*PurePosixPath(source_member).parts[1:])
            else:
                relative = Path("notices") / Path(source_member).name
            output = ROOT / "packages/core/licenses/fusion-pixel" / relative
            write_atomic(output, data)
            license_rows.append({"filename": relative.as_posix(), "path": f"licenses/fusion-pixel/{relative.as_posix()}", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "source_member": source_member})

        license_paths = [row["path"] for row in license_rows]
        for _, _, row in staged:
            row["license_dependencies"] = license_paths

        inventory = {
            "family": "Fusion Pixel",
            "version": VERSION,
            "license": "OFL-1.1",
            "license_path": "licenses/fusion-pixel/OFL.txt",
            "license_source_url": f"{RELEASE_URL}",
            "license_source_member": license_name,
            "license_sha256": hashlib.sha256(license_data).hexdigest(),
            "license_bytes": len(license_data),
            "license_notices": license_rows,
            "source_repository": "https://github.com/TakWolf/fusion-pixel-font",
            "source_release_url": RELEASE_URL,
            "fonts": [row for _, _, row in sorted(staged, key=lambda item: item[2]["path"])],
        }
        write_atomic(ROOT / ".source-cache/inventory/families/fusion-pixel.json", (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        for row in inventory["fonts"]:
            print(f"{row['path']}: {row['bytes']} bytes sha256={row['sha256']} glyphs={row['glyph_count']} BBX={row['bbx']} DWIDTH={row['dwidth_values']} region={row['region']}")
        print(f"license {license_name}: {len(license_data)} bytes sha256={inventory['license_sha256']}")
        print(f"downloaded {len(downloads)} archives ({sum(x[3] for x in downloads)} bytes); extracted {len(staged)} BDFs ({sum(len(x[1]) for x in staged)} bytes); total decompressed={extracted_total} bytes")


if __name__ == "__main__":
    main()
