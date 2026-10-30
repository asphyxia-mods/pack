#!/usr/bin/env python3
"""Markdown summary of what changed between two exported .mrpack files.

usage: report.py BASE.mrpack CANDIDATE.mrpack [--validation RESULT.json] [--client RESULT.json]
"""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path


def key(entry: dict) -> str:
    """Stable identity of a file across versions."""
    url = entry["downloads"][0]
    if m := re.match(r"https://cdn\.modrinth\.com/data/([^/]+)/", url):
        return f"modrinth:{m.group(1)}"
    if m := re.match(r"https://github\.com/([^/]+/[^/]+)/releases/", url):
        return f"github:{m.group(1)}"
    return f"path:{entry['path']}"


def load(mrpack: Path) -> tuple[dict, dict[str, tuple[str, str]]]:
    """index, and identity -> (filename, sha512) for every mod file."""
    with zipfile.ZipFile(mrpack) as z:
        index = json.loads(z.read("modrinth.index.json"))
        files = {key(f): (Path(f["path"]).name, f["hashes"]["sha512"]) for f in index["files"]}
        for n in z.namelist():
            if re.match(r"(client-)?overrides/mods/.+\.jar$", n):
                files[f"bundled:{n.split('/', 1)[1]}"] = (Path(n).name, hashlib.sha512(z.read(n)).hexdigest())
    return index, files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", type=Path)
    ap.add_argument("candidate", type=Path)
    ap.add_argument("--validation", type=Path)
    ap.add_argument("--client", type=Path, action="append", default=[], help="result.json from client-test.py (repeatable)")
    args = ap.parse_args()

    base_index, base = load(args.base)
    cand_index, cand = load(args.candidate)
    # Changed contents count as an update even when the filename stays the same.
    updated = sorted(
        (base[k][0], cand[k][0] if cand[k][0] != base[k][0] else f"{cand[k][0]} (new contents)", k.split(":")[0])
        for k in base.keys() & cand.keys() if base[k][1] != cand[k][1]
    )
    added = sorted(cand[k][0] for k in cand.keys() - base.keys())
    removed = sorted(base[k][0] for k in base.keys() - cand.keys())
    unchanged = len(base.keys() & cand.keys()) - len(updated)

    deps = cand_index["dependencies"]
    out = [
        f"## {cand_index['name']}",
        "",
        f"Minecraft {deps.get('minecraft')} · Fabric {deps.get('fabric-loader')}",
        "",
        f"**{len(cand)} files**: {len(updated)} updated "
        f"({sum(1 for u in updated if u[2] == 'github')} custom builds), "
        f"{len(added)} added, {len(removed)} removed, {unchanged} unchanged",
    ]
    if base_index["dependencies"] != deps:
        out += ["", f"Pack dependencies changed: `{base_index['dependencies']}` → `{deps}`"]
    if updated:
        out += ["", "| | from | to |", "|---|---|---|"]
        out += [f"| {src} | `{a}` | `{b}` |" for a, b, src in updated]
    for title, items in (("Added", added), ("Removed", removed)):
        if items:
            out += ["", f"**{title}:** " + ", ".join(f"`{i}`" for i in items)]

    if args.validation and args.validation.exists():
        v = json.loads(args.validation.read_text())
        out += ["", f"Dependency validation: **{'FAIL' if v['errors'] else 'PASS'}**"]
        out += [f"- ❌ {e}" for e in v["errors"]]
        out += [f"- ⚠️ {w}" for w in v["warnings"]]
    for path in args.client:
        label = path.parent.parent.name  # client-test/<backend>/results/result.json
        if path.exists():
            c = json.loads(path.read_text())
            out += ["", f"Client launch ({label}): **{'PASS' if c['ok'] else 'FAIL'}**" + (f" ({c['reason']})" if c["reason"] else "")]
        else:
            out += ["", f"Client launch ({label}): **not run**"]
    print("\n".join(out))


if __name__ == "__main__":
    main()
