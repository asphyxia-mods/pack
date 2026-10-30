#!/usr/bin/env python3
"""Import a .mrpack into a packwiz pack directory.

Modrinth-hosted files become .pw.toml metadata with [update.modrinth], so
`packwiz update` can track them. Overrides (configs etc.) are copied as-is,
except overrides/mods/*.jar, which are listed so they can be pinned by hand.

usage: import-mrpack.py PACK.mrpack PACK_DIR
"""
import json
import re
import shutil
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

CDN = re.compile(r"^https://cdn\.modrinth\.com/data/([A-Za-z0-9]+)/versions/([A-Za-z0-9]+)/")
UA = "asphyxiamywife/asphyxia-mods (import-mrpack)"


def toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def side(env: dict | None) -> str:
    env = env or {}
    client = env.get("client", "required") != "unsupported"
    server = env.get("server", "required") != "unsupported"
    return "both" if client and server else "client" if client else "server"


def fetch_projects(ids: list[str]) -> dict:
    url = "https://api.modrinth.com/v2/projects?ids=" + urllib.parse.quote(json.dumps(ids))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req) as r:
        return {p["id"]: p for p in json.load(r)}


def safe_rel(path: str) -> PurePosixPath:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        sys.exit(f"refusing unsafe path in mrpack: {path}")
    return p


def main(mrpack: str, pack_dir: str) -> None:
    out = Path(pack_dir)
    with zipfile.ZipFile(mrpack) as z:
        index = json.loads(z.read("modrinth.index.json"))

        entries = []
        for f in index["files"]:
            m = CDN.match(f["downloads"][0])
            if not m:
                print(f"skip (not Modrinth CDN): {f['path']}", file=sys.stderr)
                continue
            entries.append((f, m.group(1), m.group(2)))

        projects = fetch_projects(sorted({pid for _, pid, _ in entries}))

        for f, pid, vid in entries:
            rel = safe_rel(f["path"])
            proj = projects[pid]
            meta = out / rel.parent / f"{proj['slug']}.pw.toml"
            meta.parent.mkdir(parents=True, exist_ok=True)
            meta.write_text(
                f"name = {toml_str(proj['title'])}\n"
                f"filename = {toml_str(rel.name)}\n"
                f"side = \"{side(f.get('env'))}\"\n\n"
                "[download]\n"
                f"url = {toml_str(f['downloads'][0])}\n"
                "hash-format = \"sha512\"\n"
                f"hash = \"{f['hashes']['sha512']}\"\n\n"
                "[update]\n[update.modrinth]\n"
                f"mod-id = \"{pid}\"\n"
                f"version = \"{vid}\"\n"
            )

        loose_jars = []
        for name in z.namelist():
            if not name.startswith("overrides/") or name.endswith("/"):
                continue
            rel = safe_rel(name[len("overrides/"):])
            if rel.parts[0] == "mods" and rel.suffix == ".jar":
                loose_jars.append(str(rel))
                continue
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)

    print(f"imported {len(entries)} Modrinth files into {out}")
    for j in loose_jars:
        print(f"needs a pin (bundled jar): {j}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
