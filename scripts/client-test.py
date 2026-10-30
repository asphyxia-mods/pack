#!/usr/bin/env python3
"""Launch the real client with a .mrpack and check that it joins a world.

  1. installs the pack's client side into WORK/instance
  2. starts a vanilla dedicated server (offline mode, flat world)
  3. launches the Fabric client via portablemc, joining that server
  4. passes if the player joins, stays connected for --soak seconds, and is
     still in the server's player list (`list`) at the end

With --backend opengl|vulkan the client is forced onto that graphics backend
and the test fails if the game falls back to the other one.

Needs a display (run under xvfb-run in CI) and portablemc on PATH. Logs, crash
reports and a screenshot (if ImageMagick's `import` exists) go to WORK/results.

Downloaded game files and the server jar live in --shared (default: WORK), so
runs with different --work directories can reuse them.

usage: client-test.py PACK.mrpack [--backend B] [--work DIR] [--shared DIR] [--timeout S] [--soak S]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
PLAYER = "PackTest"
PORT = 25565


class Fail(Exception):
    pass


def get_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "asphyxia-mods (client-test)"})) as r:
        return json.load(r)


def server_jar(mc: str, dest: Path) -> Path:
    version = next(v for v in get_json(MANIFEST)["versions"] if v["id"] == mc)
    dl = get_json(version["url"])["downloads"]["server"]
    jar = dest / f"server-{mc}.jar"
    if not jar.exists() or hashlib.sha1(jar.read_bytes()).hexdigest() != dl["sha1"]:
        dest.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(dl["url"], jar)
    return jar


# The game's main thread can die while the JVM stays up; don't wait out the timeout.
FATAL = re.compile(r'Uncaught exception in thread "(main|Render thread)"|Preparing crash report')


def wait_for(log: Path, pattern: str, proc: subprocess.Popen, timeout: float,
             also: list[subprocess.Popen] = (), game_log: Path | None = None) -> bool:
    expr = re.compile(pattern)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists() and expr.search(log.read_text(errors="replace")):
            return True
        for p in (proc, *also):
            if p.poll() is not None:
                return False
        if game_log and game_log.exists() and (m := FATAL.search(game_log.read_text(errors="replace"))):
            raise Fail(f"client hit a fatal error before joining ({m.group(0)})")
        time.sleep(1)
    return False


def read_from(log: Path, offset: int) -> str:
    with open(log, "rb") as f:
        f.seek(offset)
        return f.read().decode(errors="replace")


def stop(proc: subprocess.Popen, grace: float = 20) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mrpack", type=Path)
    ap.add_argument("--work", type=Path, default=Path("client-test"))
    ap.add_argument("--shared", type=Path, help="directory for downloaded game files (default: WORK)")
    ap.add_argument("--timeout", type=float, default=900, help="seconds for the client to join")
    ap.add_argument("--soak", type=float, default=45, help="seconds to stay in-game after joining")
    ap.add_argument("--backend", choices=["default", "opengl", "vulkan"], default="default")
    args = ap.parse_args()

    work = args.work.resolve()
    shared = (args.shared or args.work).resolve()
    instance, server_dir, results = work / "instance", work / "server", work / "results"
    for d in (instance, server_dir, results):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    subprocess.run([sys.executable, "-I", SCRIPTS / "install-mrpack.py", args.mrpack, instance], check=True)
    with zipfile.ZipFile(args.mrpack) as z:
        deps = json.loads(z.read("modrinth.index.json"))["dependencies"]
    mc, loader = deps["minecraft"], deps["fabric-loader"]

    options = instance / "options.txt"
    lines = options.read_text().splitlines() if options.exists() else []
    lines = [l for l in lines if not l.startswith("preferredGraphicsBackend:")]
    options.write_text("\n".join(lines + [f'preferredGraphicsBackend:"{args.backend}"']) + "\n")

    # Vanilla server: offline so the test account can join, flat + small view for speed.
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", PORT)) == 0:
            sys.exit(f"port {PORT} is already in use; is another server or test still running?")

    jar = server_jar(mc, shared / "cache")
    (server_dir / "eula.txt").write_text("eula=true\n")
    (server_dir / "server.properties").write_text(
        f"online-mode=false\nserver-port={PORT}\nlevel-type=minecraft\\:flat\n"
        "view-distance=4\nsimulation-distance=4\nspawn-protection=0\nenforce-secure-profile=false\n"
        "white-list=false\nenforce-whitelist=false\n"
    )
    server_log = results / "server.log"
    server = subprocess.Popen(
        ["java", "-Xmx2G", "-jar", jar, "nogui"], cwd=server_dir, stdin=subprocess.PIPE,
        stdout=open(server_log, "w"), stderr=subprocess.STDOUT, start_new_session=True,
    )

    def terminated(signum, _frame):
        raise Fail(f"test interrupted by signal {signum}")

    # Turn SIGTERM/SIGINT into a failure so the finally block stops server and client.
    signal.signal(signal.SIGTERM, terminated)
    signal.signal(signal.SIGINT, terminated)

    client = None
    ok, reason, backend = False, "", None
    started = time.monotonic()
    try:
        if not wait_for(server_log, r"Done \(", server, 300):
            raise Fail("vanilla server failed to start")

        client_log = results / "client-stdout.log"
        client = subprocess.Popen(
            ["portablemc", "--main-dir", shared / "mc", "start", f"fabric:{mc}:{loader}",
             "--mc-dir", instance, "-u", PLAYER, "--join-server", "127.0.0.1",
             "--join-server-port", str(PORT), "--resolution", "1280x720",
             "--jvm-arg=-Xmx4G"],
            stdout=open(client_log, "w"), stderr=subprocess.STDOUT, start_new_session=True,
            # SDL's GLX path finds no usable visual on Xvfb; EGL works with Mesa.
            env={**os.environ, "SDL_VIDEO_FORCE_EGL": "1"},
        )

        if not wait_for(server_log, rf"\b{PLAYER} joined the game", client, args.timeout, [server],
                        game_log=instance / "logs/latest.log"):
            raise Fail("client exited before joining" if client.poll() is not None else "timed out waiting for the client to join")
        joined = time.monotonic() - started
        game_log = (instance / "logs/latest.log").read_text(errors="replace")
        if m := re.search(r"Using graphics backend (\w+)", game_log):
            backend = m.group(1)
        print(f"joined after {joined:.0f}s on {backend or 'unknown'} backend, staying in-game for {args.soak:.0f}s")
        if args.backend != "default" and (backend or "").lower() != args.backend:
            raise Fail(f"asked for {args.backend} but the game used {backend or 'an unknown backend'}")

        # Stay in-game, failing as soon as the player drops or a process dies.
        joined_at = server_log.stat().st_size
        left = re.compile(rf"\b{PLAYER} (lost connection: .*|left the game)")
        deadline = time.monotonic() + args.soak
        while time.monotonic() < deadline:
            if m := left.search(read_from(server_log, joined_at)):
                raise Fail(f"player disconnected while in-game ({m.group(1)})")
            if client.poll() is not None:
                raise Fail(f"client exited {client.returncode} while in-game")
            if server.poll() is not None:
                raise Fail("server exited while client was in-game")
            time.sleep(1)
        if shutil.which("import"):
            subprocess.run(["import", "-window", "root", results / "screenshot.png"], check=False)

        # Confirm with the server that the player is still connected.
        before_list = server_log.stat().st_size
        server.stdin.write(b"list\n")
        server.stdin.flush()
        listing = re.compile(r"players online:(.*)")
        for _ in range(15):
            time.sleep(1)
            if m := listing.search(read_from(server_log, before_list)):
                break
        else:
            raise Fail("server did not answer `list`")
        if PLAYER not in re.split(r"[,\s]+", m.group(1)):
            raise Fail(f"player no longer connected at the end (server list:{m.group(1) or ' empty'})")
        ok = True
    except Fail as e:
        reason = str(e)
    finally:
        for p in (client, server):
            if p is not None:
                stop(p)
        for src in ("logs/latest.log", "crash-reports"):
            path = instance / src
            if path.is_dir():
                shutil.copytree(path, results / path.name, dirs_exist_ok=True)
            elif path.exists():
                shutil.copy(path, results / "client.log")
        crashes = sorted((results / "crash-reports").glob("*.txt")) if (results / "crash-reports").exists() else []
        if crashes:
            ok, reason = False, reason or f"crash report written: {crashes[-1].name}"
        print(f"Client launch ({backend or args.backend}): {'PASS' if ok else 'FAIL'}" + (f" ({reason})" if reason else ""))
        (results / "result.json").write_text(json.dumps({"ok": ok, "reason": reason, "backend": backend}))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
