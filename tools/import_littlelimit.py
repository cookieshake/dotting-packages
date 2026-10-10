#!/usr/bin/env python3
"""Import the official Littlelimit Misaki and k8x12 BDF archives unchanged."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
HOST = "littlelimit.net"
USER_AGENT = "dotting-littlelimit-importer/1.0"
REQUEST_TIMEOUT = 20
MAX_REDIRECTS = 5
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 64
MAX_RATIO = 200

FAMILIES = {
    "misaki": {
        "display": "Misaki",
        "version": "2021-05-05",
        "page": "https://littlelimit.net/misaki.htm",
        "archive": "misaki_bdf_2021-05-05.zip",
        "archive_sha256": "a275f173cf5935890f84d3e65d05b1bf73028e4d4bf41cb3de0ef3b5ebe8e217",
        "base": "https://littlelimit.net/arc/misaki/",
        "fonts": {
            "misaki_gothic.bdf": "Gothic",
            "misaki_mincho.bdf": "Mincho",
            "misaki_gothic_2nd.bdf": "Gothic 2nd",
        },
        "copyright": "Copyright(C) 2002-2021 Num Kadoma",
    },
    "k8x12": {
        "display": "k8x12",
        "version": "2021-05-05",
        "page": "https://littlelimit.net/k8x12.htm",
        "archive": "k8x12_bdf_2021-05-05.zip",
        "archive_sha256": "305ab94915ec3c8e1ada08f493ee5bfdd21e2710a97d7c4d4272444d8e18ff40",
        "base": "https://littlelimit.net/arc/k8x12/",
        "fonts": {
            "k8x12.bdf": "Basic",
            "k8x12L.bdf": "L",
            "k8x12S.bdf": "S",
        },
        "copyright": "Copyright (C) 2015-2021 Num Kadoma",
    },
}

ENGLISH_NOTICES = {
    "misaki": (
        "These fonts are free softwares.",
        "Unlimited permission is granted to use, copy, and distribute it, with or without modification, either commercially and noncommercially.",
        'THESE FONTS ARE PROVIDED "AS IS" WITHOUT WARRANTY.',
    ),
    "k8x12": (
        "These fonts are free software.",
        "Unlimited permission is granted to use, copy, and distribute them, with or without modification, either commercially or noncommercially.",
        'THESE FONTS ARE PROVIDED "AS IS" WITHOUT WARRANTY.',
    ),
}
JAPANESE_NOTICES = {
    "misaki": "これらのフォントはフリー（自由な）ソフトウエアです。\nあらゆる改変の有無に関わらず、また商業的な利用であっても、自由にご利用、複製、再配布することができますが、全て無保証とさせていただきます。",
    "k8x12": "これらのフォントはフリー（自由な）ソフトウエアです。\nあらゆる改変の有無に関わらず、また商業的な利用であっても、自由にご利用、複製、再配布することができますが、全て無保証とさせていただきます。",
}


class BoundedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow only bounded HTTP(S) redirects back to the official source host."""

    max_redirections = MAX_REDIRECTS
    max_repeats = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme not in ("http", "https") or parsed.hostname != HOST or parsed.username or parsed.password:
            raise ValueError(f"redirect leaves official public origin: {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(BoundedRedirectHandler())


def fetch(url: str, limit: int) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.username or parsed.password:
        raise ValueError(f"refusing non-official source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    started = time.monotonic()
    with OPENER.open(request, timeout=REQUEST_TIMEOUT) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if final.hostname != HOST or final.scheme not in ("https", "http"):
            raise ValueError(f"response left official public origin: {response.geturl()}")
        data = response.read(limit + 1)
    if time.monotonic() - started > REQUEST_TIMEOUT * (MAX_REDIRECTS + 1):
        raise TimeoutError(f"download exceeded total deadline: {url}")
    if len(data) > limit:
        raise ValueError(f"download exceeds {limit} bytes: {url}")
    return data


def safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if "\\" in name or "\x00" in name:
        raise ValueError(f"invalid ZIP path: {name!r}")
    path = PurePosixPath(name)
    mode = info.external_attr >> 16
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe ZIP path: {name!r}")
    if (mode & 0o170000) == 0o120000:
        raise ValueError(f"symlink ZIP member rejected: {name!r}")
    if info.flag_bits & 1:
        raise ValueError(f"encrypted ZIP member rejected: {name!r}")
    if info.file_size > MAX_FILE:
        raise ValueError(f"ZIP member exceeds size limit: {name!r}")
    if info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_RATIO):
        raise ValueError(f"ZIP compression ratio exceeds limit: {name!r}")
    return path


def bdf_metadata(filename: str, data: bytes) -> dict[str, object]:
    text = data.decode("utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "STARTFONT 2.1" or lines[-1] != "ENDFONT":
        raise ValueError(f"invalid native BDF framing: {filename}")

    def one(pattern: str, label: str) -> str:
        matches = re.findall(pattern, text, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError(f"expected one {label} in {filename}; got {len(matches)}")
        return matches[0]

    font_name = one(r"^FONT\s+(.+)$", "FONT")
    bbx = [int(value) for value in one(r"^FONTBOUNDINGBOX\s+(.+)$", "FONTBOUNDINGBOX").split()]
    if len(bbx) != 4:
        raise ValueError(f"invalid FONTBOUNDINGBOX in {filename}")
    chars = int(one(r"^CHARS\s+(\d+)\s*$", "CHARS"))
    glyphs: list[dict[str, object]] = []
    for match in re.finditer(r"^STARTCHAR\s+(.+?)\s*\n(.*?)^ENDCHAR\s*$", text, re.MULTILINE | re.DOTALL):
        block = match.group(2)
        encoding = re.search(r"^ENCODING\s+(-?\d+)(?:\s+-?\d+)?\s*$", block, re.MULTILINE)
        dwidth = re.search(r"^DWIDTH\s+(-?\d+)\s+(-?\d+)\s*$", block, re.MULTILINE)
        if encoding is None or dwidth is None:
            raise ValueError(f"missing glyph encoding/DWIDTH in {filename}: {match.group(1)}")
        glyphs.append({"name": match.group(1), "encoding": int(encoding.group(1)), "dwidth": [int(dwidth.group(1)), int(dwidth.group(2))]})
    if chars != len(glyphs) or chars != len(re.findall(r"^STARTCHAR\s+", text, re.MULTILINE)):
        raise ValueError(f"CHARS/glyph record mismatch in {filename}")
    codes = sorted({int(glyph["encoding"]) for glyph in glyphs if int(glyph["encoding"]) >= 0})
    ranges: list[str] = []
    if codes:
        low = high = codes[0]
        for codepoint in codes[1:]:
            if codepoint == high + 1:
                high = codepoint
            else:
                ranges.append(f"U+{low:04X}" if low == high else f"U+{low:04X}-U+{high:04X}")
                low = high = codepoint
        ranges.append(f"U+{low:04X}" if low == high else f"U+{low:04X}-U+{high:04X}")
    return {
        "font_name": font_name,
        "bbx": bbx,
        "dwidth_values": [list(value) for value in sorted({tuple(glyph["dwidth"]) for glyph in glyphs})],
        "glyph_count": chars,
        "coverage_codepoint_count": len(codes),
        "coverage_ranges": ranges,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def write_atomic(path: Path, data: bytes) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = parent.resolve()
    if not resolved_parent.is_relative_to(ROOT) or path.is_symlink():
        raise ValueError(f"unsafe output path: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=resolved_parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def import_family(key: str, family: dict[str, object], archive: bytes) -> tuple[list[tuple[Path, bytes]], dict[str, object], int]:
    archive_sha = hashlib.sha256(archive).hexdigest()
    if archive_sha != family["archive_sha256"]:
        raise ValueError(f"official archive SHA-256 mismatch for {family['archive']}: {archive_sha}")
    if len(archive) > MAX_ARCHIVE:
        raise ValueError(f"archive exceeds limit: {family['archive']}")
    outputs: list[tuple[Path, bytes]] = []
    rows: list[dict[str, object]] = []
    expanded = 0
    with zipfile.ZipFile(__import__("io").BytesIO(archive)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError(f"too many archive members: {family['archive']}")
        by_name: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            member = safe_member(info)
            if info.is_dir():
                continue
            if member.as_posix() in by_name:
                raise ValueError(f"duplicate archive member: {member}")
            by_name[member.as_posix()] = info
        for filename, variant in family["fonts"].items():
            if filename not in by_name:
                raise ValueError(f"missing expected native BDF: {filename}")
            info = by_name[filename]
            data = zf.read(info)
            expanded += len(data)
            if len(data) > MAX_FILE or expanded > MAX_TOTAL:
                raise ValueError("archive extraction exceeds configured size limits")
            row = bdf_metadata(filename, data)
            relative = f"fonts/{key}/{filename}"
            row.update({
                "filename": filename,
                "variant": variant,
                "path": relative,
                "version": family["version"],
                "source_url": family["base"] + family["archive"],
                "source_page_url": family["page"],
                "source_archive": family["archive"],
                "source_archive_sha256": archive_sha,
                "source_member": filename,
                "source_file_sha256": hashlib.sha256(data).hexdigest(),
                "license": "Littlelimit permissive notice (see preserved full notice; not SPDX/OFL)",
                "license_path": f"licenses/{key}/NOTICE.txt",
            })
            rows.append(row)
            outputs.append((ROOT / ".source-cache" / relative, data))

        source_notice_name = "misaki.txt" if key == "misaki" else "k8x12.txt"
        if source_notice_name not in by_name:
            raise ValueError(f"missing attribution/license source text: {source_notice_name}")
        source_text = zf.read(by_name[source_notice_name]).decode("utf-8")
        normalized_lines = {line.strip() for line in source_text.splitlines()}
        if family["copyright"] not in source_text or not all(line in normalized_lines for line in ENGLISH_NOTICES[key]) or not all(line in normalized_lines for line in JAPANESE_NOTICES[key].splitlines()):
            raise ValueError(f"attribution or full English/Japanese permission notice absent in {source_notice_name}")
        # Keep the complete upstream documentation file byte-for-byte, retaining copyright,
        # exact bilingual notice, dates and context; do not substitute an SPDX license.
        notice = zf.read(by_name[source_notice_name])
    notice_path = f"licenses/{key}/NOTICE.txt"
    outputs.append((ROOT / "packages/core" / notice_path, notice))
    inventory = {
        "family": family["display"],
        "version": family["version"],
        "source_page_url": family["page"],
        "source_archive_url": family["base"] + family["archive"],
        "source_archive": family["archive"],
        "source_archive_bytes": len(archive),
        "source_archive_sha256": archive_sha,
        "copyright": family["copyright"],
        "license": "Littlelimit permissive notice; commercial and noncommercial redistribution is expressly permitted, with or without modification; provided without warranty. Not labeled OFL or SPDX.",
        "license_path": notice_path,
        "license_notice_source_member": source_notice_name,
        "license_notice_bytes": len(notice),
        "license_notice_sha256": hashlib.sha256(notice).hexdigest(),
        "fonts": rows,
    }
    return outputs, inventory, expanded


def main() -> None:
    archive_count = len(FAMILIES)
    if archive_count + sum(len(family["fonts"]) for family in FAMILIES.values()) > MAX_FILES:
        raise ValueError("configured files exceed aggregate file-count limit")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = list(pool.map(lambda family: fetch(family["base"] + family["archive"], MAX_ARCHIVE), FAMILIES.values()))
    total_downloaded = sum(len(data) for data in downloaded)
    if total_downloaded > MAX_TOTAL:
        raise ValueError("aggregate archive downloads exceed total limit")
    pending: list[tuple[str, list[tuple[Path, bytes]], dict[str, object], int]] = []
    if len(downloaded) != len(FAMILIES):
        raise ValueError("downloaded archive count does not match configured families")
    for (key, family), archive in zip(FAMILIES.items(), downloaded):
        outputs, inventory, expanded = import_family(key, family, archive)
        pending.append((key, outputs, inventory, expanded))
    # Validate everything before any asset or sidecar is replaced.
    for key, outputs, inventory, _ in pending:
        for path, data in outputs:
            write_atomic(path, data)
        sidecar = ROOT / "packages/core/inventory/families" / f"{key}.json"
        write_atomic(sidecar, (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        for row in inventory["fonts"]:
            print(f"{row['path']}: {row['bytes']} bytes sha256={row['sha256']} glyphs={row['glyph_count']} BBX={row['bbx']} DWIDTH={row['dwidth_values']} coverage={row['coverage_codepoint_count']}")
        print(f"{inventory['license_path']}: {inventory['license_notice_bytes']} bytes sha256={inventory['license_notice_sha256']}")
    print(f"total: downloaded={total_downloaded} bytes; BDFs=6; native extracted={sum(size for _, _, _, size in pending)} bytes; archive count={archive_count}")


if __name__ == "__main__":
    main()
