"""Flagship exhibition loop: a manifold that is never an empty room.

    python3 -m manifold.flagship http://localhost:8757

Rotates through the games forever, seating house pilots so there is
always a live match to watch when a visitor (or their agent) lands.
House minds default to the free mock deciders; pass --anthropic MODEL
to seat real minds instead (your key, your spend — printed up front).

This is operations, not protocol: the flagship is just a client. Any
operator can run one against their own manifold; nothing here has
privileges a stranger's script wouldn't.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

# STABLE names: identity is the unit of learning. The same aster plays
# every match, so reflection compounds in aster's playbook.md and the
# leaderboard tracks a career, not a stranger per lobby.
ROSTER = ["aster", "briar", "cove", "dune", "ember", "flint"]

EXHIBITS = {
    "convergence": {
        "params": {"round_seconds": 30, "expected_players": 3},
        "timeout": 30 * 8 + 120,      # worst case all 8 rounds + latency
    },
    "fogline": {
        "params": {"tick_seconds": 45, "expected_players": 3},
        "timeout": 45 * 6 + 120,
    },
    "prang": {
        "params": {"match_seconds": 120, "expected_players": 4},
        "timeout": 120 + 90,
    },
}


def preflight_anthropic(model: str) -> None:
    """One 1-token call before seating real minds. A dead key otherwise
    fails silently inside the pilots and the flagship plays entire
    matches of nothing but missed windows — learned the hard way."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("[flagship] --anthropic needs ANTHROPIC_API_KEY in the "
                 "environment (keys never travel; this stays client-side)")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": 1,
                         "messages": [{"role": "user", "content": "ping"}]
                         }).encode(), method="POST")
    req.add_header("x-api-key", key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("content-type", "application/json")
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError as e:
        sys.exit(f"[flagship] anthropic preflight FAILED (HTTP {e.code}): "
                 f"{e.read().decode()[:300]}\n"
                 "fix the key or credits before seating real minds")
    print(f"[flagship] anthropic preflight ok: {model} answers")


def seats_for(game: str, anthropic: str | None) -> list[tuple[str, float | None]]:
    """(decider, hz) per seat. With real minds, prang seats them beside
    scripted strikers — a fixed baseline is how improvement becomes
    measurable, and an API mind at 60Hz is neither possible nor the
    point: it plays through longer committed programs at ~0.5Hz."""
    a = f"anthropic:{anthropic}" if anthropic else None
    if game == "convergence":
        return [(a or "mock:converge", None)] * 3
    if game == "fogline":
        return ([(a, None)] * 3 if a else
                [("mock:fogline-brash", None), ("mock:fogline-measured", None),
                 ("mock:hold", None)])
    # prang: seat parity is team assignment (even=west, odd=east), so
    # [a, s, a, s] means west = the reasoning minds, east = the tuned
    # scripted strikers: a fixed baseline the LLM team must learn to beat
    if a:
        return [(a, 0.5), ("mock:prang-striker", 3),
                (a, 0.5), ("mock:prang-striker", 3)]
    return [("mock:prang-striker", 3)] * 4


def http(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("ngrok-skip-browser-warning", "1")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def run_match(server: str, game: str, spec: dict, env: dict,
              anthropic: str | None) -> None:
    code = http("POST", f"{server}/games/{game}/lobbies",
                {"params": spec["params"]})["code"]
    print(f"[flagship] {game} {code} — curtain up "
          f"({server}/watch/{game}/{code})")
    logdir = os.path.join(env["MANIFOLD_HOME"], "logs")
    os.makedirs(logdir, exist_ok=True)
    pilots = []
    for i, (decider, hz) in enumerate(seats_for(game, anthropic)):
        name = ROSTER[i]
        subprocess.run([sys.executable, "-m", "manifold_cli", "join",
                        server, game, "--code", code, "--name", name],
                       env=env, capture_output=True)
        cmd = [sys.executable, "-m", "manifold_cli", "pilot", "--as", name,
               "--decider", decider]
        if hz:
            cmd += ["--hz", str(hz)]
        log = open(os.path.join(logdir, f"{name}.log"), "a")
        pilots.append((subprocess.Popen(cmd, env=env, stdout=log,
                                        stderr=subprocess.STDOUT), log))
    deadline = time.time() + spec["timeout"]
    result = None
    while time.time() < deadline:
        try:
            st = http("GET", f"{server}/games/{game}/lobbies/{code}/state")
            if st["phase"] == "done":
                result = st.get("result")
                break
        except Exception:
            pass
        time.sleep(5)
    # pilots reflect AFTER done (that's the learning); give them room
    grace = time.time() + 90
    for p, log in pilots:
        while p.poll() is None and time.time() < grace:
            time.sleep(2)
        if p.poll() is None:
            p.terminate()
        log.close()
    print(f"[flagship] {game} {code} — "
          f"{json.dumps(result)[:140] if result else 'timed out (pilots reaped)'}")


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)   # logs stream under nohup
    ap = argparse.ArgumentParser(prog="manifold.flagship")
    ap.add_argument("server")
    ap.add_argument("--interval", type=float, default=90,
                    help="seconds between matches (default 90)")
    ap.add_argument("--cycles", type=int, default=0,
                    help="stop after N matches (0 = run forever)")
    ap.add_argument("--games", default="convergence,prang,fogline")
    ap.add_argument("--anthropic", metavar="MODEL",
                    help="seat anthropic:<MODEL> house minds instead of "
                         "mocks — burns YOUR api credits every match")
    a = ap.parse_args()

    server = a.server.rstrip("/")
    games = [g.strip() for g in a.games.split(",") if g.strip() in EXHIBITS]
    if not games:
        sys.exit(f"no known games in --games; choose from {list(EXHIBITS)}")
    if a.anthropic:
        preflight_anthropic(a.anthropic)
        print(f"[flagship] house minds: anthropic:{a.anthropic} — this "
              "spends real credits continuously; Ctrl-C is the off switch")
    env = {**os.environ,
           "MANIFOLD_HOME": os.environ.get(
               "FLAGSHIP_HOME", os.path.expanduser("~/.manifold-flagship"))}

    n = 0
    while True:
        game = games[n % len(games)]
        try:
            run_match(server, game, EXHIBITS[game], env, a.anthropic)
        except Exception as e:
            print(f"[flagship] {game} skipped: {e}")
        n += 1
        if a.cycles and n >= a.cycles:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
