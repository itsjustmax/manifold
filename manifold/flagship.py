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

ROSTER = ["aster", "briar", "cove", "dune", "ember", "flint"]

EXHIBITS = {
    "convergence": {
        "params": {"round_seconds": 20, "expected_players": 3},
        "seats": [("mock:converge", None)] * 3,
        "timeout": 20 * 8 + 40,
    },
    "fogline": {
        "params": {"tick_seconds": 45, "expected_players": 3},
        "seats": [("mock:fogline-brash", None), ("mock:fogline-measured", None),
                  ("mock:hold", None)],
        "timeout": 45 * 6 + 60,
    },
    "prang": {
        "params": {"match_seconds": 120, "expected_players": 4},
        "seats": [("mock:prang-chase", 3)] * 4,
        "timeout": 120 + 45,
    },
}


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
    pilots = []
    for i, (decider, hz) in enumerate(spec["seats"]):
        name = f"{ROSTER[i]}-{code.split('-')[1]}"
        subprocess.run([sys.executable, "-m", "manifold_cli", "join",
                        server, game, "--code", code, "--name", name],
                       env=env, capture_output=True)
        cmd = [sys.executable, "-m", "manifold_cli", "pilot", "--as", name,
               "--decider", anthropic or decider]
        if hz:
            cmd += ["--hz", str(hz)]
        pilots.append(subprocess.Popen(cmd, env=env,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL))
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
    for p in pilots:
        if p.poll() is None:
            p.terminate()
    print(f"[flagship] {game} {code} — "
          f"{json.dumps(result)[:140] if result else 'timed out (pilots reaped)'}")


def main() -> int:
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
