"""Manifold kit: everything every game needs and no game owns.

Lobbies, boarding tokens, the hash-chained event log, long-poll
sequencing, comms budgets, and spectator redaction. Games plug in
through the Game interface at the bottom.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Optional

FRAME_HZ = 60
GENESIS = "0" * 64
_WORDS = ["GALE", "REEF", "SPAR", "KEEL", "FATH", "BRIG", "MOOR", "WAKE",
          "HELM", "DRIFT", "SHOAL", "BUOY"]


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def now() -> float:
    return time.time()


class Event:
    __slots__ = ("seq", "prev", "hash", "frame", "kind", "actor", "public", "data", "ts")

    def __init__(self, seq: int, prev: str, frame: int, kind: str,
                 actor: Optional[str], public: bool, data: dict):
        self.seq, self.prev, self.frame = seq, prev, frame
        self.kind, self.actor, self.public, self.data = kind, actor, public, data
        self.ts = round(now(), 3)
        body = {"seq": seq, "prev": prev, "frame": frame, "kind": kind,
                "actor": actor, "public": public, "data": data, "ts": self.ts}
        self.hash = sha256(prev + canonical(body))

    def full(self) -> dict:
        return {"seq": self.seq, "prev": self.prev, "hash": self.hash,
                "frame": self.frame, "kind": self.kind, "actor": self.actor,
                "public": self.public, "data": self.data, "ts": self.ts}

    def redacted(self) -> dict:
        d = self.full()
        if not self.public:
            d = {**d, "data": "[sealed]"}
        return d

    @staticmethod
    def from_dict(d: dict) -> "Event":
        """Rehydrate a journaled event verbatim — hash included, never
        recomputed, so a restored chain is the original chain."""
        ev = object.__new__(Event)
        ev.seq, ev.prev, ev.hash = d["seq"], d["prev"], d["hash"]
        ev.frame, ev.kind, ev.actor = d["frame"], d["kind"], d["actor"]
        ev.public, ev.data, ev.ts = d["public"], d["data"], d["ts"]
        return ev


def verify_chain(events: list[dict]) -> dict:
    """Recompute the chain. Sealed entries can't be recomputed until
    unsealed; they are counted, not failed."""
    prev, sealed, checked = GENESIS, 0, 0
    for e in events:
        if e.get("data") == "[sealed]":
            sealed += 1
            prev = e["hash"]
            continue
        body = {k: e[k] for k in ("seq", "prev", "frame", "kind", "actor",
                                   "public", "data", "ts")}
        if e["prev"] != prev or sha256(e["prev"] + canonical(body)) != e["hash"]:
            return {"ok": False, "failed_at_seq": e["seq"], "checked": checked}
        prev = e["hash"]
        checked += 1
    return {"ok": True, "checked": checked, "sealed_unverified": sealed}


class Player:
    def __init__(self, name: str, seat: int, team: Optional[str], token: str):
        self.name, self.seat, self.team, self.token = name, seat, team, token


class Comms:
    """Budgeted channels. Config comes from the game's manifest fragment."""

    def __init__(self, channels: list[dict], window_frames: int):
        self.cfg = {c["id"]: c for c in channels}
        self.window_frames = max(1, window_frames)
        self.spent: dict[tuple, int] = {}
        self.messages: list[dict] = []

    def say(self, lobby: "Lobby", player: Player, channel: str, text: str) -> Optional[str]:
        c = self.cfg.get(channel)
        if c is None:
            return f"no such channel '{channel}'"
        text = str(text)
        budget = int(c.get("budget_chars_per_window", 280))
        key = (player.name, channel, lobby.frame() // self.window_frames)
        used = self.spent.get(key, 0)
        if used + len(text) > budget:
            return (f"channel '{channel}' budget: {budget} chars per window, "
                    f"{budget - used} left")
        self.spent[key] = used + len(text)
        msg = {"channel": channel, "from": player.name, "team": player.team,
               "text": text, "frame": lobby.frame(), "scope": c.get("scope", "all"),
               "disclosure": c.get("disclosure", "live")}
        self.messages.append(msg)
        lobby.emit("say", c.get("disclosure", "live") == "live" and c.get("scope") == "all",
                   player.name, {"channel": channel, "text": text})
        return None

    def visible_to(self, player: Optional[Player], done: bool) -> list[dict]:
        out = []
        for m in self.messages[-60:]:
            if done:
                out.append(m)
            elif m["disclosure"] != "live":
                continue
            elif m["scope"] == "all":
                out.append(m)
            elif m["scope"] == "team" and player is not None and player.team == m["team"]:
                out.append(m)
        return [{k: m[k] for k in ("channel", "from", "text", "frame")} for m in out]


class Lobby:
    def __init__(self, code: str, game: "Game", params: dict):
        self.code = code
        self.game = game
        self.params = params
        self.phase = "lobby"  # lobby | running | done
        self.created_at = now()
        self.players: dict[str, Player] = {}     # by token
        self.by_name: dict[str, Player] = {}
        self.events: list[Event] = []
        self.seq = 0
        self.cond: asyncio.Condition = asyncio.Condition()
        self.started_at: Optional[float] = None
        self.result: Optional[dict] = None
        self.comms = Comms(game.comms_channels(), game.comms_window_frames())
        self.task: Optional[asyncio.Task] = None
        self.on_done: Optional[Callable[["Lobby"], None]] = None
        self.journal: Optional[Path] = None   # durable event log (crash recovery)

    def journal_write(self, obj: dict) -> None:
        if self.journal is None:
            return
        try:
            with self.journal.open("a") as f:
                f.write(canonical(obj) + "\n")
                f.flush()
        except OSError:
            pass    # a full disk must not kill a live match

    # ------------------------------------------------------------ timing
    def frame(self) -> int:
        if self.started_at is None:
            return 0
        fo = getattr(self.game, "frame_override", None)
        if fo is not None:
            return fo
        return int((now() - self.started_at) * FRAME_HZ)

    # ------------------------------------------------------------ events
    def emit(self, kind: str, public: bool, actor: Optional[str], data: dict) -> Event:
        prev = self.events[-1].hash if self.events else GENESIS
        ev = Event(self.seq + 1, prev, self.frame(), kind, actor, public, data)
        self.events.append(ev)
        self.seq = ev.seq
        self.journal_write(ev.full())
        self._wake()
        return ev

    def touch(self) -> None:
        """Bump seq without an event (realtime frame progress)."""
        self.seq += 1
        self._wake()

    def _wake(self) -> None:
        async def _notify():
            async with self.cond:
                self.cond.notify_all()
        try:
            asyncio.get_running_loop().create_task(_notify())
        except RuntimeError:
            pass

    async def wait_seq(self, since: int, timeout: float) -> None:
        deadline = now() + min(timeout, 25.0)
        async with self.cond:
            while self.seq <= since and now() < deadline:
                try:
                    await asyncio.wait_for(self.cond.wait(), deadline - now())
                except asyncio.TimeoutError:
                    return

    # ------------------------------------------------------------- join
    def join(self, name: str) -> Player:
        if self.phase != "lobby":
            raise KitError("match already started; joining is closed")
        name = str(name).strip()[:24]
        if not name or not all(ch.isalnum() or ch in "-_" for ch in name):
            raise KitError("name must be 1-24 chars of letters, digits, - or _")
        if name in self.by_name:
            raise KitError(f"name '{name}' is taken in this lobby")
        maxp = self.game.players_max()
        if len(self.players) >= maxp:
            raise KitError(f"lobby full ({maxp})")
        seat = len(self.players)
        team = self.game.assign_team(seat, self.params)
        p = Player(name, seat, team, secrets.token_urlsafe(24))
        self.players[p.token] = p
        self.by_name[name] = p
        self.journal_write({"kind": "_session", "name": name, "seat": seat,
                            "team": team, "token": p.token})
        self.emit("join", True, name, {"seat": seat, "team": team})
        expected = int(self.params.get("expected_players",
                                       self.game.players_min()))
        if len(self.players) >= expected:
            self.start()
        return p

    def start(self) -> None:
        if self.phase != "lobby":
            return
        self.phase = "running"
        self.started_at = now()
        self.game.on_start(list(self.players.values()), self)
        self.task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        try:
            await self.game.run(self)
        except Exception as e:  # referee crash is a public fact
            self.emit("referee_error", True, None, {"error": repr(e)})
        self.result = self.game.result()
        self.phase = "done"
        self.emit("done", True, None, {"result": self.result})
        if self.on_done is not None:
            try:
                self.on_done(self)
            except Exception:
                pass

    # ------------------------------------------------------------- state
    def snapshot(self, player: Optional[Player], since_hint: int = 0) -> dict:
        done = self.phase == "done"
        return {
            "manifold": "0.1",
            "game": self.game.game_id(),
            "code": self.code,
            "seq": self.seq,
            "phase": self.phase,
            "frame": self.frame(),
            "players": [{"name": p.name, "seat": p.seat, "team": p.team}
                        for p in self.players.values()],
            "you": ({"name": player.name, "seat": player.seat,
                     "team": player.team,
                     "committed": self.game.committed(player)}
                    if player else None),
            "view": self.game.view(player, self),
            "comms": self.comms.visible_to(player, done),
            "deadline_utc": self.game.deadline_utc(),
            "result": self.result,
        }

    def log_entries(self) -> list[dict]:
        done = self.phase == "done"
        return [e.full() if done else e.redacted() for e in self.events]


class KitError(Exception):
    pass


class Game:
    """The whole contract between a game and the manifold."""

    ID = "game"
    NAME = "Game"
    VERSION = "1.0"
    SKILLS: list[str] = []

    # --- static description ---
    def game_id(self) -> str: return self.ID
    def rulebook(self) -> str: raise NotImplementedError
    def players_min(self) -> int: return 2
    def players_max(self) -> int: return 100
    def timing(self) -> dict: raise NotImplementedError
    def actions_schema(self) -> dict: raise NotImplementedError
    def comms_channels(self) -> list[dict]: return []
    def comms_window_frames(self) -> int: return FRAME_HZ * 2
    def observation_hint(self) -> int: return 400

    def manifest(self, base: str) -> dict:
        rb = self.rulebook()
        return {
            "manifold": "0.1",
            "game": {"id": self.ID, "name": self.NAME, "version": self.VERSION},
            "rulebook": {"url": f"{base}/games/{self.ID}/rulebook.md",
                         "sha256": sha256(rb)},
            "skills": self.SKILLS,
            "timing": self.timing(),
            "players": {"min": self.players_min(), "max": self.players_max(),
                        "teams": self.assign_team(0, {}) is not None},
            "comms": {"verbs": (["say"] if self.comms_channels() else []),
                      "reserved_verbs": ["claim"],
                      "channels": self.comms_channels()},
            "actions": {"schema": self.actions_schema()},
            "observation": {"contract": "O(1)",
                            "budget_tokens_hint": self.observation_hint()},
            "endpoints": {"lobbies": f"{base}/games/{self.ID}/lobbies"},
        }

    # --- lifecycle ---
    def assign_team(self, seat: int, params: dict) -> Optional[str]: return None
    def on_start(self, players: list[Player], lobby: Lobby) -> None: ...
    async def run(self, lobby: Lobby) -> None: raise NotImplementedError
    def result(self) -> dict: return {}

    # --- per-request ---
    def on_action(self, player: Player, action: dict, lobby: Lobby,
                  reasoning: str = "") -> dict:
        """Return a verdict dict: {"accepted": bool, ...}. Synchronous, so
        rejection reasons ride the same HTTP response."""
        raise NotImplementedError

    def view(self, player: Optional[Player], lobby: Lobby) -> dict: return {}
    def committed(self, player: Player) -> bool: return False
    def deadline_utc(self) -> Optional[str]: return None

    def settle_from_record(self, events: list[dict], params: dict,
                           players: list[Player]) -> dict:
        """Called at boot for a match found mid-flight in a journal:
        produce an honest final result from the recorded events alone.
        Games override; the default admits it recovered nothing."""
        return {"aborted": True,
                "note": "manifold restarted mid-match; this game defines "
                        "no record-settlement, so nothing was recovered"}


def new_code(existing: set[str]) -> str:
    for _ in range(200):
        c = f"{secrets.choice(_WORDS)}-{secrets.randbelow(90) + 10}"
        if c not in existing:
            return c
    return secrets.token_hex(4).upper()


def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
