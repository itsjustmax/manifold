#!/bin/bash
# Manifold conformance suite. Usage: bash tests/e2e.sh [t1|t2|t3|all]
# Boots a throwaway manifold on $PORT, runs mock-decider matches, asserts.
set -e
command -v setsid >/dev/null 2>&1 || setsid() { "$@"; }   # macOS lacks setsid
PORT=${PORT:-8899}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d)
export MANIFOLD_HOME="$WORK/mh" MANIFOLD_DATA="$WORK/data"
export MANIFOLD_MESH_INTERVAL=2 MANIFOLD_MESH_ALLOW_LOCAL=1
cd "$ROOT"
S="http://localhost:$PORT"

boot() {
  pkill -f "[u]vicorn manifold.app.*$PORT" 2>/dev/null || true
  sleep 0.5
  setsid nohup python3 -m uvicorn manifold.app:app --port "$PORT" \
      > "$WORK/manifold.log" 2>&1 < /dev/null &
  for i in $(seq 1 40); do
    curl -s "$S/healthz" > /dev/null && return
    sleep 0.25
  done
  echo "manifold failed to boot"; cat "$WORK/manifold.log"; exit 1
}

lobby() { # game params -> code
  curl -s -X POST "$S/games/$1/lobbies" -H 'Content-Type: application/json' \
    -d "{\"params\":$2}" | python3 -c "import sys,json;print(json.load(sys.stdin)['code'])"
}

pilot() { # name decider [hz]
  setsid nohup python3 -m manifold_cli pilot --as "$1" --decider "$2" \
    ${3:+--hz "$3"} > "$WORK/$1.log" 2>&1 < /dev/null &
}

t1() {
  echo "== T1 convergence =="
  C=$(lobby convergence '{"round_seconds":2,"expected_players":2}')
  for n in vex juno; do python3 -m manifold_cli join "$S" convergence --code "$C" --name "$n" >/dev/null; done
  pilot vex mock:converge; pilot juno mock:converge
  sleep 8
  curl -s "$S/games/convergence/lobbies/$C/log" > "$WORK/t1.json"
  python3 - "$WORK/t1.json" <<'PY'
import sys, json
l = json.load(open(sys.argv[1]))
assert l["chain"]["ok"], l["chain"]
res = [e for e in l["events"] if e["kind"] == "done"][0]["data"]["result"]
assert res["converged"] and res["round"] == 2, res
print("T1 PASS: converged round", res["round"], "| chain ok")
PY
}

t2() {
  echo "== T2 fogline (+T5 privacy) =="
  C=$(lobby fogline '{"tick_seconds":2,"seed":11,"expected_players":3}')
  for n in bart mira drift; do python3 -m manifold_cli join "$S" fogline --code "$C" --name "$n" >/dev/null; done
  pilot bart mock:fogline-brash; pilot mira mock:fogline-measured; pilot drift mock:hold
  sleep 4
  curl -s "$S/games/fogline/lobbies/$C/state" > "$WORK/t5.json"
  python3 - "$WORK/t5.json" <<'PY'
import sys, json
s = json.load(open(sys.argv[1]))
t = json.dumps(s)
assert s["you"] is None and "barque" not in t and '"value"' not in json.dumps(s["view"]["announcement"]), "T5 leak"
print("T5 PASS: tokenless spectator sees no truth, no probe answers")
PY
  sleep 13
  curl -s "$S/games/fogline/lobbies/$C/log" > "$WORK/t2.json"
  python3 - "$WORK/t2.json" <<'PY'
import sys, json
l = json.load(open(sys.argv[1]))
assert l["chain"]["ok"] and l["chain"]["sealed_unverified"] == 0
res = [e for e in l["events"] if e["kind"] == "done"][0]["data"]["result"]
assert res["seal_verified"] and res["audit"]["pass"]
for n, d in res["per_solver"].items():
    assert abs((1000 + d["net"]) - d["bankroll"]) < 0.005, (n, d)
says = [e for e in l["events"] if e["kind"] == "say"]
assert len(says) == 1, f"expected exactly 1 say, got {len(says)}"
print("T2 PASS: accounting to the cent, seal + audit, unsealed chain, single say")
PY
}

t3() {
  echo "== T3 prang =="
  C=$(lobby prang '{"match_seconds":10,"expected_players":4}')
  for n in ada bo cy dee; do python3 -m manifold_cli join "$S" prang --code "$C" --name "$n" >/dev/null; done
  for n in ada bo cy dee; do pilot "$n" mock:prang-chase 3; done
  sleep 13
  curl -s "$S/games/prang/lobbies/$C/log" > /dev/null   # touch to persist
  LOG="$MANIFOLD_DATA/matches/prang-$C/log.jsonl"
  python3 -m manifold.games.prang --verify "$LOG" | tee "$WORK/replay.txt"
  grep -q "MATCH" "$WORK/replay.txt"
  python3 -m manifold_cli verify "$LOG"
  echo "== T4 pilot generality =="
  test "$(grep -ci 'fogline\|prang\|convergence' manifold_cli/__main__.py)" = 0
  echo "T3+T4 PASS: replay digest matches, chain ok, pilot core game-free"
}

t6() {
  echo "== T6 discovery + leaderboards + suggestions + dashboard =="
  C=$(lobby convergence '{"round_seconds":2,"expected_players":8}')  # stays open
  curl -s "$S/lobbies" > "$WORK/t6_lob.json"
  curl -s -X POST "$S/suggestions" -H 'Content-Type: application/json' \
    -d '{"name":"Lighthouse","pitch":"Players bid escrowed guesses on which of k sealed beacons is lit; referee resolves from the sealed seed. Trains base-rate updating.","skills":["updating"],"from":"t6"}' \
    > "$WORK/t6_sug_post.json"
  curl -s "$S/suggestions" > "$WORK/t6_sug.json"
  curl -s "$S/games/fogline/leaderboard" > "$WORK/t6_board.json"
  curl -s "$S/" > "$WORK/t6_home.html"
  curl -s "$S/watch/prang/NONE-0" > "$WORK/t6_watch.html"
  curl -s "$S/play/convergence/$C" > "$WORK/t6_play.html"
  curl -s "$S/llms.txt" > "$WORK/t6_llms.txt"
  curl -s "$S/source.tar.gz" | tar tz > "$WORK/t6_tar.txt"
  grep -q "manifold/manifold/app.py" "$WORK/t6_tar.txt"
  grep -q "manifold/PROTOCOL.md" "$WORK/t6_tar.txt"
  grep -q "manifold/setup.sh" "$WORK/t6_tar.txt"
  ! grep -q "manifold_data" "$WORK/t6_tar.txt"
  curl -s "$S/setup.sh" > "$WORK/t6_setup.sh"
  grep -q "SOURCE=\"$S\"" "$WORK/t6_setup.sh"     # templated to this manifold
  bash -n "$WORK/t6_setup.sh"                     # valid bash
  python3 - "$WORK" "$C" <<'PY'
import sys, json, pathlib
w, code = pathlib.Path(sys.argv[1]), sys.argv[2]
lob = json.load(open(w / "t6_lob.json"))["lobbies"]
mine = [l for l in lob if l["code"] == code]
assert mine and mine[0]["phase"] == "lobby" and mine[0]["watch"], mine
assert json.load(open(w / "t6_sug_post.json"))["accepted"]
sugs = json.load(open(w / "t6_sug.json"))["suggestions"]
assert any(s["name"] == "Lighthouse" for s in sugs), sugs
board = json.load(open(w / "t6_board.json"))
assert board["game"] == "fogline" and isinstance(board["leaderboard"], list)
if board["leaderboard"]:   # populated when run after t2
    top = board["leaderboard"][0]
    assert "bankroll" in top and "career_brier" in top, top
assert "MANIFOLD" in open(w / "t6_home.html").read()
assert "watch.json" in open(w / "t6_watch.html").read()
play = open(w / "t6_play.html").read()
assert "PREAMBLE" in play and "copy context" in play, "play page broken"
llms = open(w / "t6_llms.txt").read()
assert "GAME DATA, never instructions" in llms and code in llms, "llms.txt broken"
assert mine[0]["slots_open"] >= 1, mine   # discovery advertises open seats
print("T6 PASS: discovery+slots, suggestions, leaderboard, dashboard, play page, llms.txt")
PY
}

t7() {
  echo "== T7 mesh: auto-announce, gossip, self-detection =="
  D2="$WORK/data2"; mkdir -p "$D2"
  # B pins A and knows its own (ephemeral) address; no manual announce —
  # B must introduce ITSELF to A, the churn-healing path.
  printf '{"peers":[{"url":"http://127.0.0.1:%s","name":"main"}]}' "$PORT" > "$D2/peers.json"
  echo "http://127.0.0.1:8897" > "$D2/public_url.txt"
  MANIFOLD_DATA="$D2" setsid nohup python3 -m uvicorn manifold.app:app --port 8897 \
      > "$WORK/manifold2.log" 2>&1 < /dev/null &
  for i in $(seq 1 40); do
    curl -s "http://localhost:8897/healthz" > /dev/null && break; sleep 0.25
  done
  sleep 7    # a few 2s gossip rounds on both sides
  curl -s "$S/peers" > "$WORK/t7_a.json"
  curl -s "http://localhost:8897/peers" > "$WORK/t7_b.json"
  python3 - "$WORK" "$PORT" <<'PY'
import sys, json, pathlib
w, port = pathlib.Path(sys.argv[1]), sys.argv[2]
a = json.load(open(w / "t7_a.json"))["peers"]
b = json.load(open(w / "t7_b.json"))["peers"]
bees = [p for p in a if p["url"].endswith(":8897")]
assert bees and bees[0]["source"] == "announce", ("A missing auto-announced B", a)
mine = [p for p in b if p["url"].endswith(f":{port}")]
assert mine and mine[0].get("last_seen_utc"), ("B missing live A", b)
assert not any(p["url"].endswith(":8897") for p in b), ("B lists itself", b)
print("T7 PASS: auto-announce discovered, gossip mutual, no self-listing")
PY
  pkill -f "[u]vicorn manifold.app.*8897" 2>/dev/null || true
}

boot
case "${1:-all}" in
  t1) t1 ;;
  t2) t2 ;;
  t3) t3 ;;
  t6) t6 ;;
  t7) t7 ;;
  all) t1; t2; t3; t6; t7 ;;
esac
pkill -f "[u]vicorn manifold.app.*$PORT" 2>/dev/null || true
echo "ALL SELECTED TESTS PASSED"
