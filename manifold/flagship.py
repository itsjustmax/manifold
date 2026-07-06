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

def exhibit_for(game: str, minds: str | None) -> dict:
    """Window sizes follow the mind: plan-billed CLIs (claude-code,
    codex) think in tens of seconds, so turn windows widen for them."""
    cli = bool(minds) and not minds.startswith("anthropic:")
    if game == "convergence":
        rs = 75 if cli else 30
        return {"params": {"round_seconds": rs, "expected_players": 3},
                "timeout": rs * 8 + 120}
    if game == "fogline":
        ts = 90 if cli else 45
        return {"params": {"tick_seconds": ts, "expected_players": 3},
                "timeout": ts * 6 + 120}
    if game == "prang2":
        return {"params": {"match_seconds": 120, "expected_players": 6},
                "timeout": 120 + 90}
    return {"params": {"match_seconds": 120, "expected_players": 4},
            "timeout": 120 + 90}


def preflight_cli(spec: str) -> None:
    """Plan-billed mind: verify the CLI exists and answers before
    seating it. No key involved anywhere."""
    import shutil
    binary = "claude" if spec.startswith("claude-code") else "codex"
    if shutil.which(binary) is None:
        sys.exit(f"[flagship] '{binary}' CLI not found — install it and "
                 "log in (plan-billed, no API key), or use mock minds")
    out = subprocess.run([binary] + (["-p"] if binary == "claude" else ["exec"]),
                         input="Reply with exactly: OK",
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0 or "OK" not in out.stdout:
        sys.exit(f"[flagship] {binary} preflight failed: "
                 f"{(out.stderr or out.stdout)[:200]}")
    print(f"[flagship] {spec} preflight ok — plan-billed, no API key")


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


def seats_for(game: str, minds: str | None) -> list[tuple[str, float | None]]:
    """(decider, hz) per seat. Real minds take the turn games; in
    prang, only API minds (sub-second-capable at 0.5Hz through
    committed programs) get seats — a CLI mind thinks in tens of
    seconds and realtime will not wait for it. Seat parity is team
    assignment (even=west, odd=east), so [m, s, m, s] means west = the
    reasoning minds vs east = the tuned scripted strikers: a fixed
    baseline the minds must learn to beat."""
    if game == "convergence":
        return [(minds or "mock:converge", None)] * 3
    if game == "fogline":
        return ([(minds, None)] * 3 if minds else
                [("mock:fogline-brash", None), ("mock:fogline-measured", None),
                 ("mock:hold", None)])
    if game == "prang2":
        return [("mock:paddle", 3)] * 6      # 3v3, one agent per paddle
    if minds and minds.startswith("anthropic:"):
        return [(minds, 0.5), ("mock:prang-striker", 3),
                (minds, 0.5), ("mock:prang-striker", 3)]
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
              minds: str | None) -> None:
    code = http("POST", f"{server}/games/{game}/lobbies",
                {"params": spec["params"]})["code"]
    print(f"[flagship] {game} {code} — curtain up "
          f"({server}/watch/{game}/{code})")
    logdir = os.path.join(env["MANIFOLD_HOME"], "logs")
    os.makedirs(logdir, exist_ok=True)
    pilots = []
    for i, (decider, hz) in enumerate(seats_for(game, minds)):
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
    ap.add_argument("--games", default="convergence,prang,fogline,prang2")
    ap.add_argument("--minds", metavar="DECIDER",
                    help="house minds for turn games: claude-code[:model] "
                         "or codex[:model] (plan-billed, NO API key), or "
                         "anthropic:<model> (raw API, needs credits)")
    ap.add_argument("--anthropic", metavar="MODEL",
                    help="shorthand for --minds anthropic:<MODEL>")
    a = ap.parse_args()

    server = a.server.rstrip("/")
    known = ("convergence", "prang", "fogline", "prang2")
    games = [g.strip() for g in a.games.split(",") if g.strip() in known]
    if not games:
        sys.exit(f"no known games in --games; choose from {list(known)}")
    minds = a.minds or (f"anthropic:{a.anthropic}" if a.anthropic else None)
    if minds and minds.startswith("anthropic:"):
        preflight_anthropic(minds.split(":", 1)[1])
        print(f"[flagship] house minds: {minds} — this spends API credits "
              "continuously; Ctrl-C is the off switch")
    elif minds:
        preflight_cli(minds)
        print(f"[flagship] house minds: {minds} — billed to that CLI's "
              "plan; realtime prang keeps scripted strikers (a CLI mind "
              "thinks in tens of seconds)")
    env = {**os.environ,
           "MANIFOLD_HOME": os.environ.get(
               "FLAGSHIP_HOME", os.path.expanduser("~/.manifold-flagship"))}

    n = 0
    while True:
        game = games[n % len(games)]
        try:
            run_match(server, game, exhibit_for(game, minds), env, minds)
        except Exception as e:
            print(f"[flagship] {game} skipped: {e}")
        n += 1
        if a.cycles and n >= a.cycles:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
