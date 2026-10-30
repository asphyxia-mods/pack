#!/usr/bin/env python3
"""Pin mods built from GitHub Releases (forks/ports) in a packwiz pack.

packwiz's own GitHub updater always takes a repo's newest release, which is
wrong for repos publishing builds for several Minecraft versions. Instead,
PACK_DIR/forks.toml says which release assets belong to this pack, and this
script writes a plain url+sha256 .pw.toml for the newest matching asset.

usage:
  update-forks.py PACK_DIR            update every fork to its newest match
  update-forks.py PACK_DIR --check    report only, change nothing
  update-forks.py PACK_DIR --set NAME=TAG [...]
                                      pin NAME to the matching asset of TAG

Set GITHUB_TOKEN to avoid API rate limits.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"


def gh_get_all(url: str) -> list:
    """GET a list endpoint, following Link: rel="next" through every page."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "asphyxia-mods"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    items = []
    while url:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as r:
            items += json.load(r)
            m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
            url = m.group(1) if m else None
    return items


def releases(repo: str) -> list[dict]:
    rels = gh_get_all(f"{API}/repos/{repo}/releases?per_page=100")
    rels = [r for r in rels if not r["draft"]]
    return sorted(rels, key=lambda r: r["published_at"] or "", reverse=True)


def pick(fork: dict, rels: list[dict], tag: str | None) -> tuple[dict, dict]:
    expr = re.compile(fork["asset"])
    for rel in rels:
        if tag is not None and rel["tag_name"] != tag:
            continue
        if tag is None and rel["prerelease"] and not fork.get("prerelease", False):
            continue
        assets = [a for a in rel["assets"] if expr.search(a["name"])]
        if len(assets) > 1:
            sys.exit(f"{fork['name']}: {rel['tag_name']} has several assets matching {fork['asset']!r}")
        if assets:
            return rel, assets[0]
    where = f"release {tag}" if tag else "any release"
    sys.exit(f"{fork['name']}: no asset in {where} of {fork['repo']} matches {fork['asset']!r}")


def sha256_of(asset: dict) -> str:
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.removeprefix("sha256:")
    h = hashlib.sha256()
    with urllib.request.urlopen(asset["browser_download_url"]) as r:
        for chunk in iter(lambda: r.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def current(meta: Path) -> tuple[str, str] | None:
    """(tag, filename) of the currently pinned build, if any."""
    if not meta.exists():
        return None
    data = tomllib.loads(meta.read_text())
    m = re.search(r"/releases/download/([^/]+)/", data["download"]["url"])
    return (urllib.parse.unquote(m.group(1)) if m else "?", data["filename"])


def write_meta(meta: Path, fork: dict, asset: dict) -> None:
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        f"name = {json.dumps(fork['title'])}\n"
        f"filename = {json.dumps(asset['name'])}\n"
        f"side = \"{fork.get('side', 'both')}\"\n\n"
        "[download]\n"
        f"url = {json.dumps(asset['browser_download_url'])}\n"
        "hash-format = \"sha256\"\n"
        f"hash = \"{sha256_of(asset)}\"\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack_dir", type=Path)
    ap.add_argument("--check", action="store_true", help="report available updates without writing")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=TAG", help="pin a fork to a release tag")
    args = ap.parse_args()

    forks = tomllib.loads((args.pack_dir / "forks.toml").read_text())["fork"]
    by_name = {f["name"]: f for f in forks}
    pins = dict(s.split("=", 1) for s in args.set)
    if unknown := set(pins) - set(by_name):
        sys.exit(f"unknown fork(s): {', '.join(sorted(unknown))}")
    selected = [by_name[n] for n in pins] if pins else forks

    changed = 0
    for fork in selected:
        meta = args.pack_dir / "mods" / f"{fork['name']}.pw.toml"
        rel, asset = pick(fork, releases(fork["repo"]), pins.get(fork["name"]))
        cur = current(meta)
        if cur and cur[1] == asset["name"] and cur[0] == rel["tag_name"]:
            print(f"  unchanged  {fork['name']}  {asset['name']}")
            continue
        changed += 1
        old = f"{cur[1]} ({cur[0]})" if cur else "(new)"
        print(f"  {'available' if args.check else 'updated'}  {fork['name']}  {old} -> {asset['name']} ({rel['tag_name']})")
        if not args.check:
            write_meta(meta, fork, asset)

    print(f"{changed} fork build(s) {'available' if args.check else 'changed'}")


if __name__ == "__main__":
    main()
