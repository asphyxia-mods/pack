#!/usr/bin/env python3
"""Install the client side of a .mrpack into a game directory.

Downloads every file not marked client-unsupported (cached by sha512, hash
verified), then applies overrides/ and client-overrides/.

usage: install-mrpack.py PACK.mrpack GAME_DIR [--cache DIR]
"""
import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def safe_rel(path: str) -> PurePosixPath:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        sys.exit(f"refusing unsafe path in mrpack: {path}")
    return p


def fetch(entry: dict, cache: Path) -> Path:
    sha512 = entry["hashes"]["sha512"]
    path = cache / sha512
    if not path.exists():
        req = urllib.request.Request(entry["downloads"][0], headers={"User-Agent": "asphyxia-mods (install-mrpack)"})
        with urllib.request.urlopen(req) as r:
            data = r.read()
        if hashlib.sha512(data).hexdigest() != sha512:
            sys.exit(f"{entry['path']}: sha512 mismatch with manifest")
        cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mrpack", type=Path)
    ap.add_argument("game_dir", type=Path)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache/asphyxia-mods/jars")
    args = ap.parse_args()

    with zipfile.ZipFile(args.mrpack) as z:
        index = json.loads(z.read("modrinth.index.json"))
        count = 0
        for entry in index["files"]:
            if entry.get("env", {}).get("client") == "unsupported":
                continue
            dest = args.game_dir / safe_rel(entry["path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fetch(entry, args.cache), dest)
            count += 1
        for prefix in ("overrides/", "client-overrides/"):
            for name in z.namelist():
                if name.startswith(prefix) and not name.endswith("/"):
                    dest = args.game_dir / safe_rel(name[len(prefix):])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    count += 1

    print(f"installed {count} files into {args.game_dir}")
    print(json.dumps(index["dependencies"]))


if __name__ == "__main__":
    main()
