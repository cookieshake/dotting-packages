#!/usr/bin/env python3
"""Import remaining pinned native-BDF Latin bitmap fonts with strict bounds."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
PIN = "3255e8259bc9b880c60ab8b737ec8aa574e00d75"
UA = {"User-Agent": "dotting-font-importer/1.0", "Accept": "application/vnd.github+json"}

FAMILIES = {
    "tamzen": {"repo": "sunaku/tamzen-font", "ref": PIN,
        "wanted": re.compile(r".*\.bdf$", re.I),
        "license": "https://raw.githubusercontent.com/sunaku/tamzen-font/"+PIN+"/LICENSE"},
    "bitocra": {"repo": "ninjaaron/bitocra", "ref": None,
        "wanted": re.compile(r".*\.bdf$", re.I), "license": None},
    "gohu": {"repo": "hchargois/gohufont", "ref": "cc36b8c9fed7141763e55dcee0a97abffcf08224",
        "wanted": re.compile(r".*\.bdf$", re.I), "license": None},
    "scientifica": {"repo": "oppiliappan/scientifica", "ref": "v2.3",
        "wanted": re.compile(r".*\.bdf$", re.I), "license": None},
}

def fetch(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"response exceeds bound ({limit}): {url}")
    return data

def api_json(url: str):
    return json.loads(fetch(url, 8 * 1024 * 1024))

def atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing symlink: {path}")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="."+path.name+".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def bdf_metrics(data: bytes):
    text = data.decode("ascii")
    if not text.startswith("STARTFONT "):
        raise ValueError("not a native BDF")
    def one(name):
        values = re.findall(r"(?m)^"+name+r"\s+(.+)$", text)
        if len(values) != 1: raise ValueError(f"invalid/missing {name}")
        return values[0]
    count = int(one("CHARS")); glyphs = re.findall(r"(?m)^STARTCHAR\s+(.+)$", text)
    if count != len(glyphs): raise ValueError("CHARS does not match actual glyph records")
    widths = re.findall(r"(?m)^DWIDTH\s+(.+)$", text)
    if not widths: raise ValueError("missing per-glyph DWIDTH records")
    encodings = [int(n) for n in re.findall(r"(?m)^ENCODING\s+(-?\d+)", text) if int(n) >= 0]
    return {"font_name": one("FONT"), "font_bounding_box": one("FONTBOUNDINGBOX"),
            "default_width": widths[0], "glyph_width_variants": sorted(set(widths)), "glyph_count": count,
            "encoding_count": len(encodings), "encoding_min": min(encodings) if encodings else None,
            "encoding_max": max(encodings) if encodings else None,
            "ascent": int(one("FONT_ASCENT")), "descent": int(one("FONT_DESCENT"))}

def family_files(name, spec):
    repo, ref = spec["repo"], spec["ref"]
    base = f"https://api.github.com/repos/{repo}"
    if name == "scientifica":
        release = api_json(base+"/releases/tags/v2.3")
        asset=next((a for a in release.get("assets",[]) if a["name"]=="scientifica.tar"),None)
        if not asset: return [], {"tag":release.get("tag_name"),"status":"release-asset-missing"}
        import io, tarfile
        blob=fetch(asset["browser_download_url"],MAX_TOTAL)
        if len(blob)!=asset["size"]: raise ValueError("release archive size mismatch")
        archive=tarfile.open(fileobj=io.BytesIO(blob),mode="r:")
        members=archive.getmembers()
        if len(members)>2000 or sum(max(0,m.size) for m in members)>MAX_TOTAL: raise ValueError("release archive limits exceeded")
        files=[]
        for m in members:
            if m.issym() or m.islnk() or ".." in Path(m.name).parts: raise ValueError("unsafe release archive member")
            if not m.isfile() or m.size>MAX_FILE or not m.name.lower().endswith(".bdf"): continue
            f=archive.extractfile(m)
            if f: files.append((m.name,"release-asset:"+asset["browser_download_url"],release.get("tag_name","v2.3"),f.read(MAX_FILE+1)))
        license_data=None; license_path=None
        for m in members:
            if m.isfile() and Path(m.name).name.lower() in ("license", "copying", "copying-license", "ofl.txt", "ofl"):
                f=archive.extractfile(m)
                if f: license_data=f.read(128*1024+1); license_path=m.name
        return files, {"tag":release.get("tag_name"),"asset":asset["name"],"archive_sha256":hashlib.sha256(blob).hexdigest(),"archive_bytes":len(blob),"license_data":license_data,"license_path":license_path}
    meta = api_json(base+"/commits/"+ref if ref else base)
    revision = meta["sha"] if ref else meta.get("default_branch", "main")
    tree = api_json(base+"/git/trees/"+revision+"?recursive=1")
    found = [(x["path"], f"https://raw.githubusercontent.com/{repo}/{revision}/{x['path']}", revision)
             for x in tree.get("tree", []) if x.get("type")=="blob" and spec["wanted"].search(x["path"]) and x["path"].lower().endswith(".bdf")]
    return found, {"revision": revision, "truncated": tree.get("truncated", False)}

def main():
    results=[]; total=0
    for family, spec in FAMILIES.items():
        try:
            candidates, source_info = family_files(family, spec)
        except Exception as e:
            results.append({"family":family,"status":"source-discovery-failed","detail":str(e)}); continue
        # Filter on repository paths, not marketing/style strings in FONT headers.
        # BDF XLFD names commonly encode size and style differently from filenames.
        if family == "tamzen":
            candidates=[(p,u,v) for p,u,v in candidates if re.search(r"(?:Tamzen(?:ForPowerline)?)(?:5x9|6x12|7x13|7x14|8x15|8x16|10x20)[rb]\.bdf$",Path(p).name,re.I)]
        if family == "gohu":
            candidates=[(p,u,v) for p,u,v in candidates if Path(p).name.lower().endswith(".bdf")]
        family_rows=[]
        def get(item):
            path,url,version=item[:3]
            if len(item)>3: return item,item[3],None
            try: return item,fetch(url,MAX_FILE),None
            except Exception as e: return item,None,str(e)
        with ThreadPoolExecutor(max_workers=4) as pool:
            fetched=list(pool.map(get,candidates))
        for item,data,error in fetched:
            upath,url,version=item[:3]
            if data is None:
                family_rows.append({"upstream_path":upath,"source_url":url,"status":"download-failed","detail":error}); continue
            if total+len(data)>MAX_TOTAL: raise ValueError("aggregate BDF bound exceeded")
            total+=len(data)
            try: metrics=bdf_metrics(data)
            except Exception as e:
                family_rows.append({"upstream_path":upath,"source_url":url,"status":"invalid-bdf","detail":str(e)}); continue
            dest_rel=Path(".source-cache/fonts")/family/Path(upath).name
            family_tag=Path(upath).name.lower()
            requested = {
                "tamzen": re.search(r"(?:5x9|6x12|7x13|7x14|8x15|8x16|10x20)",family_tag),
                "bitocra": True,
                "gohu": True,
                "scientifica": re.search(r"scientifica.*\.bdf$",family_tag),
            }[family]
            if not requested: continue
            dest=ROOT/dest_rel
            exists=dest.exists()
            if exists and hashlib.sha256(dest.read_bytes()).digest() != hashlib.sha256(data).digest():
                raise ValueError(f"source staging collision; existing original preserved: {dest}")
            if not exists: atomic(dest,data)
            family_rows.append({"upstream_path":upath,"path":dest_rel.as_posix(),"source_url":url,"version":version,
                "id":"fonts-"+family+"-"+re.sub(r"[^a-z0-9]+","-",Path(upath).stem.lower()).strip("-"),
                "coverage":"native-BDF-encoding-inventory","sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data),"status":"preserved-existing" if exists else "imported",**metrics})
        lic_url=spec.get("license")
        if not lic_url and family != "scientifica":
            # License filename is determined from this single repository tree response.
            treeinfo=source_info
            revision=treeinfo.get("revision", "main")
            license_name="COPYING-LICENSE" if family == "gohu" else "LICENSE"
            lic_url=f"https://raw.githubusercontent.com/{spec['repo']}/{revision}/{license_name}"
        if family == "scientifica":
            # The official release tar omits the notice; use the tag's actual LICENSE path.
            lic_url=f"https://raw.githubusercontent.com/{spec['repo']}/v2.3/LICENSE"
        license_row={"source_url":lic_url,"status":"missing"}
        if lic_url:
            try:
                license_data=source_info.get("license_data") if family == "scientifica" and source_info.get("license_data") else fetch(lic_url,128*1024)
                if len(license_data)>MAX_TOTAL-total: raise ValueError("aggregate bound exceeded")
                atomic(ROOT/"packages/core/licenses"/family/"LICENSE.txt",license_data)
                license_row={"source_url":lic_url,"sha256":hashlib.sha256(license_data).hexdigest(),"bytes":len(license_data),"status":"captured"}
            except Exception as e: license_row={"source_url":lic_url,"status":"unavailable","detail":str(e)}
        results.append({"family":family,"repository":spec["repo"],"source":source_info,"license":license_row,
                        "requested_file_pattern":spec["wanted"].pattern,
                         "missing_or_unresolved":([] if family in ("tamzen", "bitocra", "gohu") else
                             (["Official release archive did not contain a license notice"] if license_row["status"]!="captured" else [])),
                        "status":"complete" if family_rows and all(r["status"] in ("imported","preserved-existing") for r in family_rows) and license_row["status"]=="captured" else "partial","files":family_rows})
        inv=ROOT/"packages/core/inventory/families"/(family+".json")
        atomic(inv,(json.dumps(results[-1],indent=2,sort_keys=True)+"\n").encode())
    print(json.dumps({"total_bdf_bytes":total,"families":results},indent=2))

if __name__ == "__main__": main()
