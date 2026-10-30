#!/usr/bin/env python3
"""Statically validate an exported .mrpack.

Downloads every file (cached by sha512), verifies hashes, then reads each
fabric.mod.json, including jar-in-jar, and resolves mods roughly the way
Fabric Loader does: every id (and every "provides" alias) may have several
candidates, top-level or nested, and the highest version is the one loaded.
For every loaded mod, nested ones included, it checks:
  - hash mismatches / unreadable jars                    (error)
  - the same mod id shipped by two top-level jars         (error)
  - "depends" missing, or present in a version outside the range (error)
  - "breaks" a mod present in a matching version          (error)
  - "conflicts" with a mod present in a matching version  (warning)
minecraft, fabricloader and java are checked against the pack's versions.

Range matching is an approximation of Fabric's; versions it can't parse are
reported as warnings.

usage: validate-pack.py PACK.mrpack [--cache DIR] [--json OUT]
"""
import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

JAVA = "25"  # runtime the pack targets
# Supplied by Fabric Loader itself; version not known statically.
LOADER_BUNDLED = {"mixinextras"}


def parse_version(v: str):
    """(core, prerelease) like Fabric's SemanticVersion, or None if not semver-ish."""
    v = v.split("+", 1)[0]
    core, dash, pre = v.partition("-")
    parts = core.split(".")
    if not all(p.isdigit() for p in parts):
        return None
    if not dash:
        pre_key = None
    else:  # "1.2.3-" has an empty prerelease, sorting below every real one
        pre_key = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".") if p)
    return tuple(int(p) for p in parts), pre_key


def cmp_version(a, b) -> int:
    (ca, pa), (cb, pb) = a, b
    n = max(len(ca), len(cb))
    ca, cb = ca + (0,) * (n - len(ca)), cb + (0,) * (n - len(cb))
    if ca != cb:
        return -1 if ca < cb else 1
    if pa == pb:
        return 0
    if pa is None or pb is None:  # a release sorts after its prereleases
        return 1 if pa is None else -1
    return -1 if pa < pb else 1


def matches_one(version: str, pred: str) -> bool | None:
    pred = pred.strip()
    if pred in ("", "*"):
        return True
    m = re.match(r"^(>=|<=|>|<|=|~|\^)?(.+)$", pred)
    op, target = m.group(1) or "=", m.group(2)
    v = parse_version(version)
    if re.fullmatch(r"\d+(\.\d+)*\.[xX*]", target):  # 1.2.x means >=1.2.0- <1.3.0-
        if v is None:
            return None
        core = [int(p) for p in target[:-2].split(".")]
        upper = (tuple(core[:-1] + [core[-1] + 1]), ())
        return cmp_version(v, (tuple(core), ())) >= 0 and cmp_version(v, upper) < 0
    t = parse_version(target)
    if v is None or t is None:
        return version == target if op == "=" else None
    c = cmp_version(v, t)
    if op in ("~", "^"):
        upper = list(t[0]) + [0] * (2 - len(t[0])) if len(t[0]) < 2 else list(t[0])
        upper = [upper[0] + 1] if op == "^" else [upper[0], upper[1] + 1]
        return c >= 0 and cmp_version(v, (tuple(upper), ())) < 0
    return {">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0, "=": c == 0}[op]


def matches(version: str, spec) -> bool | None:
    """Fabric range: a list is OR, spaces within a string are AND."""
    results = []
    for alt in spec if isinstance(spec, list) else [spec]:
        parts = [matches_one(version, p) for p in alt.split()] or [True]
        if False in parts:
            results.append(False)
        else:
            results.append(None if None in parts else True)
    if True in results:
        return True
    return None if None in results else False


class _Key:
    """Sort key wrapper for parse_version() results."""
    def __init__(self, v):
        self.v = v

    def __lt__(self, other):
        return cmp_version(self.v, other.v) < 0

    def __eq__(self, other):
        return cmp_version(self.v, other.v) == 0


def fetch(entry: dict, cache: Path) -> bytes:
    sha512 = entry["hashes"]["sha512"]
    path = cache / sha512
    if path.exists():
        return path.read_bytes()
    req = urllib.request.Request(entry["downloads"][0], headers={"User-Agent": "asphyxia-mods (validate-pack)"})
    with urllib.request.urlopen(req) as r:
        data = r.read()
    if hashlib.sha512(data).hexdigest() != sha512:
        raise ValueError("sha512 mismatch with manifest")
    cache.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def mod_infos(data: bytes, depth: int = 0) -> list[dict]:
    """fabric.mod.json of this jar followed by those of its nested jars."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            info = json.loads(z.read("fabric.mod.json").decode("utf-8-sig"), strict=False)
        except KeyError:
            return []
        infos = [info]
        if depth < 4:
            for nested in info.get("jars", []):
                try:
                    infos += mod_infos(z.read(nested["file"]), depth + 1)
                except (KeyError, zipfile.BadZipFile):
                    pass
        return infos


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mrpack", type=Path)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache/asphyxia-mods/jars")
    ap.add_argument("--json", type=Path, help="write a machine-readable result here")
    args = ap.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    jars: dict[str, list[dict]] = {}

    with zipfile.ZipFile(args.mrpack) as z:
        index = json.loads(z.read("modrinth.index.json"))
        for entry in index["files"]:
            if entry["path"].endswith(".jar") and entry.get("env", {}).get("client") != "unsupported":
                try:
                    jars[entry["path"]] = mod_infos(fetch(entry, args.cache))
                except Exception as e:  # noqa: BLE001 - report every bad file, keep going
                    errors.append(f"{entry['path']}: {e}")
        for name in z.namelist():
            if name.startswith(("overrides/mods/", "client-overrides/mods/")) and name.endswith(".jar"):
                try:
                    jars[name.split("/", 1)[1]] = mod_infos(z.read(name))
                except zipfile.BadZipFile as e:
                    errors.append(f"{name}: {e}")

    mc = index["dependencies"].get("minecraft")
    loader = index["dependencies"].get("fabric-loader")

    # Candidates per mod id: (version, top_level, jar path, fabric.mod.json).
    candidates: dict[str, list[tuple[str, bool, str, dict]]] = {}
    top_level: dict[str, str] = {}
    for path, infos in jars.items():
        if not infos:
            warnings.append(f"{path}: no fabric.mod.json")
            continue
        top = infos[0]["id"]
        if top in top_level:
            errors.append(f"duplicate mod id {top!r}: {top_level[top]} and {path}")
        top_level[top] = path
        for i, info in enumerate(infos):
            for mid in [info["id"], *info.get("provides", [])]:
                candidates.setdefault(mid, []).append((str(info.get("version", "")), i == 0, path, info))

    def rank(c):
        v = parse_version(c[0])
        return (v is not None, v and _Key(v), c[1])

    # The loaded candidate per id; plus what the game and loader provide.
    loaded = {mid: max(cands, key=rank) for mid, cands in candidates.items()}
    available = {mid: (c[0], c[2]) for mid, c in loaded.items()}
    available.update({
        "minecraft": (mc, "game"), "java": (JAVA, "runtime"),
        "fabricloader": (loader, "loader"), "fabric-loader": (loader, "loader"),
    })

    checked = set()
    for mid, (_, _, path, info) in loaded.items():
        if id(info) in checked or info["id"] != mid:
            continue  # each mod once, under its own id rather than an alias
        checked.add(id(info))
        who = f"{info['id']} {info.get('version', '')} ({path})"
        for dep, spec in info.get("depends", {}).items():
            if dep in LOADER_BUNDLED:
                continue
            if dep not in available:
                errors.append(f"{who} requires missing mod {dep!r} {spec!r}")
                continue
            version, where = available[dep]
            hit = matches(version, spec)
            if hit is False:
                errors.append(f"{who} requires {dep} {spec!r}, but {where} has {version}")
            elif hit is None:
                warnings.append(f"{who} requires {dep} {spec!r}; couldn't compare with version {version!r}")
        for kind, sink in (("breaks", errors), ("conflicts", warnings)):
            for other, spec in info.get(kind, {}).items():
                if other not in available or other == info["id"]:
                    continue
                version, where = available[other]
                hit = matches(version, spec)
                if hit:
                    sink.append(f"{who} {kind} {other} {version} ({where}), range {spec!r}")
                elif hit is None:
                    warnings.append(f"{who} {kind} {other} {spec!r}; couldn't compare with version {version!r}")
    print(f"{index['name']} {index['versionId']}  (Minecraft {mc}, Fabric {loader})")
    print(f"{len(jars)} mod jars, {len(checked)} mods loaded incl. nested, {len(candidates)} ids incl. provides")
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print("Dependency validation:", "FAIL" if errors else "PASS")

    if args.json:
        args.json.write_text(json.dumps({"errors": errors, "warnings": warnings, "jars": len(jars)}, indent=2))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
