#!/usr/bin/env python3
"""Make a .mrpack self-contained for everything not hosted on Modrinth.

Files downloaded from anywhere but cdn.modrinth.com (e.g. fork builds on
GitHub Releases) are fetched, verified against the manifest hashes, and moved
into the archive as overrides; Modrinth files stay as downloads. Launchers
such as Prism warn about non-Modrinth downloads, but not about overrides.

Placement follows each file's env: client-only files go to client-overrides/,
server-only to server-overrides/, everything else to overrides/.

usage: embed-external.py IN.mrpack OUT.mrpack [--cache DIR]
"""
import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

MODRINTH_CDN = "cdn.modrinth.com"


def safe_rel(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        sys.exit(f"refusing unsafe path in mrpack: {path}")
    return str(p)


def fetch(entry: dict, cache: Path) -> bytes:
    sha512 = entry["hashes"]["sha512"]
    path = cache / sha512
    if path.exists():
        data = path.read_bytes()
    else:
        req = urllib.request.Request(entry["downloads"][0], headers={"User-Agent": "asphyxia-mods (embed-external)"})
        with urllib.request.urlopen(req) as r:
            data = r.read()
    if hashlib.sha512(data).hexdigest() != sha512 or hashlib.sha1(data).hexdigest() != entry["hashes"]["sha1"]:
        sys.exit(f"{entry['path']}: download does not match the manifest hashes")
    cache.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def overrides_dir(env: dict | None) -> str:
    env = env or {}
    if env.get("server") == "unsupported":
        return "client-overrides"
    if env.get("client") == "unsupported":
        return "server-overrides"
    return "overrides"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dest", type=Path)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache/asphyxia-mods/jars")
    args = ap.parse_args()

    with zipfile.ZipFile(args.src) as src:
        index = json.loads(src.read("modrinth.index.json"))
        names = set(src.namelist())
        kept, embedded = [], {}
        for entry in index["files"]:
            if urllib.parse.urlparse(entry["downloads"][0]).hostname == MODRINTH_CDN:
                kept.append(entry)
                continue
            target = f"{overrides_dir(entry.get('env'))}/{safe_rel(entry['path'])}"
            if target in names or target in embedded:
                sys.exit(f"{target} already exists in the pack")
            embedded[target] = fetch(entry, args.cache)
        index["files"] = kept

        with zipfile.ZipFile(args.dest, "w", zipfile.ZIP_DEFLATED) as out:
            out.writestr("modrinth.index.json", json.dumps(index, indent=1))
            for info in src.infolist():
                if info.filename != "modrinth.index.json":
                    out.writestr(info, src.read(info.filename))
            for target, data in sorted(embedded.items()):
                out.writestr(target, data)

    for target in sorted(embedded):
        print(f"embedded {target}")
    print(f"{len(embedded)} file(s) embedded, {len(kept)} left as Modrinth downloads -> {args.dest}")


if __name__ == "__main__":
    main()
