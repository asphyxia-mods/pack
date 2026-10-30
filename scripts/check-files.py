#!/usr/bin/env python3
"""Check that every non-mod file in a pack belongs to a mod that is still in it.

PACK_DIR/owners.toml maps path globs to mods/<name>.pw.toml names (or a list
of them, or "minecraft"/"pack"). Errors on:
  - files no rule claims                (add a rule)
  - files whose owning mods are all gone (orphaned config; --prune deletes)
  - rules that match no file             (stale rule; --prune deletes)

usage: check-files.py PACK_DIR [--prune]

After `packwiz remove some-mod`, run with --prune, then `packwiz refresh`.
"""
import argparse
import fnmatch
import re
import sys
import tomllib
from pathlib import Path

BUILTIN_OWNERS = {"minecraft", "pack"}
PACKWIZ_FILES = {"pack.toml", "index.toml", ".packwizignore"}


def pack_files(pack: Path) -> list[str]:
    ignore = PACKWIZ_FILES | {
        line.strip() for line in (pack / ".packwizignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    } if (pack / ".packwizignore").exists() else PACKWIZ_FILES
    files = []
    for p in sorted(pack.rglob("*")):
        rel = p.relative_to(pack).as_posix()
        if not p.is_file() or any(fnmatch.fnmatchcase(rel, pat) for pat in ignore):
            continue
        if rel.startswith("mods/") and (rel.endswith(".pw.toml") or rel.endswith(".jar")):
            continue  # the mods themselves
        files.append(rel)
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack_dir", type=Path)
    ap.add_argument("--prune", action="store_true", help="delete orphaned files and stale rules")
    args = ap.parse_args()
    pack = args.pack_dir
    owners_path = pack / "owners.toml"

    rules = {pat: [o] if isinstance(o, str) else o for pat, o in tomllib.loads(owners_path.read_text()).items()}
    mods = {p.name.removesuffix(".pw.toml") for p in (pack / "mods").glob("*.pw.toml")}
    mods |= {p.stem for p in (pack / "mods").glob("*.jar")}
    present = mods | BUILTIN_OWNERS

    unowned, orphaned, used = [], [], set()
    for rel in pack_files(pack):
        pats = [pat for pat in rules if fnmatch.fnmatchcase(rel, pat)]
        if not pats:
            unowned.append(rel)
            continue
        used.update(pats)
        owners = {o for pat in pats for o in rules[pat]}
        if not owners & present:
            orphaned.append((rel, sorted(owners)))

    dead_rules = {pat for pat, owners in rules.items() if not set(owners) & present}
    stale = sorted((set(rules) - used) | dead_rules)

    for rel in unowned:
        print(f"error: {rel} has no owner in owners.toml")
    for rel, owners in orphaned:
        print(f"{'removed' if args.prune else 'error'}: {rel} (owner {', '.join(owners)} is no longer in the pack)")
    for pat in stale:
        print(f"{'removed' if args.prune else 'error'}: owners.toml rule {pat!r} is stale")
    for pat, owners in rules.items():
        if pat not in dead_rules and (gone := set(owners) - present):
            print(f"warning: rule {pat!r} lists mods not in the pack: {', '.join(sorted(gone))}")

    if args.prune and (orphaned or stale):
        for rel, _ in orphaned:
            (pack / rel).unlink()
            parent = (pack / rel).parent
            while parent != pack and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        drop = re.compile(r'^"(%s)"\s*=' % "|".join(re.escape(p) for p in stale)) if stale else None
        lines = owners_path.read_text().splitlines(keepends=True)
        owners_path.write_text("".join(l for l in lines if not (drop and drop.match(l))))
        print("pruned; run `packwiz refresh`")

    bad = unowned or (not args.prune and (orphaned or stale))
    print("File ownership:", "FAIL" if bad else "PASS")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
