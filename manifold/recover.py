"""Boot-time recovery: a manifold forgets nothing it journaled.

Every lobby appends a durable journal (header, boarding sessions,
every hash-chained event) as it runs. At boot we scan for journals
with no `done` event and split two ways:

- never started  -> restore the lobby whole: same code, same seated
  players, same boarding tokens; joining simply continues.
- mid-match      -> settle from the record: emit `referee_restart`,
  let the game compute an honest aborted result from its events, emit
  `done`, persist. The restored chain is the original chain — hashes
  are rehydrated, never recomputed — so `manifold verify` still passes.

Full mid-match *resume* is future work; settling cleanly is the
guarantee that matters first: no stranded tokens, no phantom money,
no match that never ends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .kit import Event, Game, Lobby, Player, now

START_MARKERS_EXCLUDED = ("join", "say")   # anything else means the game began


def recover_live(data: Path, games: dict[str, type[Game]],
                 on_done: Callable[[Lobby], None]) -> dict[tuple, Lobby]:
    out: dict[tuple, Lobby] = {}
    live = data / "live"
    if not live.exists():
        return out
    for j in sorted(live.glob("*.jsonl")):
        try:
            lines = [json.loads(l) for l in j.read_text().splitlines()
                     if l.strip()]
        except (json.JSONDecodeError, OSError):
            j.rename(j.with_suffix(".corrupt"))
            continue
        header = next((l for l in lines if l.get("kind") == "_lobby"), None)
        if header is None or header.get("game") not in games:
            j.rename(j.with_suffix(".corrupt"))
            continue
        events = [l for l in lines
                  if not str(l.get("kind", "")).startswith("_")]
        if any(e["kind"] == "done" for e in events):
            j.unlink()          # finished normally; matches/ already has it
            continue

        gid, code = header["game"], header["code"]
        params = header.get("params", {})
        game = games[gid]()
        lb = Lobby(code, game, params)
        lb.journal = j
        lb.on_done = on_done
        for s in (l for l in lines if l.get("kind") == "_session"):
            p = Player(s["name"], s["seat"], s.get("team"), s["token"])
            lb.players[p.token] = p
            lb.by_name[p.name] = p
        lb.events = [Event.from_dict(e) for e in events]
        lb.seq = lb.events[-1].seq if lb.events else 0

        started = any(e["kind"] not in START_MARKERS_EXCLUDED
                      for e in events)
        if not started:
            lb.phase = "lobby"      # fully joinable again, tokens intact
            out[(gid, code)] = lb
            continue

        lb.phase = "running"
        lb.started_at = now()
        lb.emit("referee_restart", True, None,
                {"note": "manifold restarted mid-match; settling from "
                         "the record"})
        lb.result = game.settle_from_record(
            events, params, list(lb.players.values()))
        lb.phase = "done"
        lb.emit("done", True, None, {"result": lb.result})
        try:
            on_done(lb)
        except Exception:
            pass
        out[(gid, code)] = lb
    return out
