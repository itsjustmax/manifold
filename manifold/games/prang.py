"""Prang: agent soccer at 60Hz.

The world never waits. Minds act at whatever rate they can afford by
committing INPUT PROGRAMS — timed macro segments that execute at frame
precision until replaced. Physics is a pure step function shared by the
live loop and the replay verifier: spawn + frame-stamped input log
re-simulates to an identical digest.

Replay check:  python -m manifold.games.prang --verify <log.jsonl>
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time

from ..kit import FRAME_HZ, Game, Lobby, Player, canonical

FIELD_W, FIELD_H = 2000.0, 1200.0
GOAL_Y = (450.0, 750.0)
VESSEL_R, BALL_R = 20.0, 12.0
THRUST_A = 0.0012          # accel per thrust-percent per frame
DRAG, BALL_DRAG = 0.98, 0.992
WALL_BOUNCE = 0.85
KICK_DIST, KICK_IMPULSE = 38.0, 7.0
MAX_PROGRAM_MS, MAX_SEGMENTS = 1000, 5
CORNER_CUT = 150.0          # 45° corner bevels: nothing wedges flush
_SQRT2 = 2.0 ** 0.5


def _bevel(o: dict, r: float, bounce: float) -> None:
    """Deflect a circle of radius r off the four 45° corner cuts.
    Local frame per corner: lu, lv grow toward the field; the cut is
    the line lu + lv = CORNER_CUT."""
    for cx, cy in ((0.0, 0.0), (0.0, FIELD_H),
                   (FIELD_W, 0.0), (FIELD_W, FIELD_H)):
        su = 1.0 if cx == 0.0 else -1.0
        sv = 1.0 if cy == 0.0 else -1.0
        lu, lv = su * (o["x"] - cx), sv * (o["y"] - cy)
        if lu > CORNER_CUT or lv > CORNER_CUT:
            continue
        need = CORNER_CUT + r * _SQRT2
        s = lu + lv
        if s >= need:
            continue
        push = (need - s) / 2.0
        lu += push
        lv += push
        vlu, vlv = su * o["vx"], sv * o["vy"]
        vn = (vlu + vlv) / 2.0          # velocity into the cut, halved
        if vn < 0:
            vlu -= (1.0 + bounce) * vn
            vlv -= (1.0 + bounce) * vn
        o["x"], o["y"] = cx + su * lu, cy + sv * lv
        o["vx"], o["vy"] = su * vlu, sv * vlv


# ---------------------------------------------------------------- world
def build_world(roster: list[dict]) -> dict:
    """roster: [{name, team}] with team in {'west','east'}."""
    vessels = {}
    for team, home_x, ang in (("west", 400.0, 0.0), ("east", 1600.0, 180.0)):
        members = [r for r in roster if r["team"] == team]
        for i, r in enumerate(members):
            y = FIELD_H * (i + 1) / (len(members) + 1)
            vessels[r["name"]] = {
                "x": home_x, "y": y, "vx": 0.0, "vy": 0.0, "ang": ang,
                "team": team, "prog": [], "seg_left": 0, "kicked": False,
                "spawn": (home_x, y, ang)}
    return {"frame": 0, "score": {"west": 0, "east": 0},
            "ball": {"x": FIELD_W / 2, "y": FIELD_H / 2, "vx": 0.0, "vy": 0.0},
            "vessels": vessels}


def apply_program(world: dict, name: str, segments: list[dict]) -> None:
    v = world["vessels"][name]
    v["prog"] = [{"frames": max(1, round(s["ms"] * FRAME_HZ / 1000)),
                  "thrust": float(s.get("thrust", 0)),
                  "turn": float(s.get("turn", 0)) / FRAME_HZ,
                  "kick": bool(s.get("kick", False))} for s in segments]
    v["seg_left"] = v["prog"][0]["frames"] if v["prog"] else 0
    v["kicked"] = False


def _reset_positions(world: dict) -> None:
    world["ball"].update(x=FIELD_W / 2, y=FIELD_H / 2, vx=0.0, vy=0.0)
    for v in world["vessels"].values():
        sx, sy, sa = v["spawn"]
        v.update(x=sx, y=sy, vx=0.0, vy=0.0, ang=sa,
                 prog=[], seg_left=0, kicked=False)


def physics_step(world: dict) -> str | None:
    """Advance one frame. Returns 'west'/'east' if that team scored."""
    b = world["ball"]
    for name in sorted(world["vessels"]):
        v = world["vessels"][name]
        seg = v["prog"][0] if v["prog"] else None
        if seg is not None:
            v["ang"] = (v["ang"] + seg["turn"]) % 360
            a = seg["thrust"] * THRUST_A
            rad = math.radians(v["ang"])
            v["vx"] += a * math.cos(rad)
            v["vy"] += a * math.sin(rad)
            if seg["kick"] and not v["kicked"]:
                if math.hypot(b["x"] - v["x"], b["y"] - v["y"]) <= KICK_DIST:
                    b["vx"] = KICK_IMPULSE * math.cos(rad) + 0.5 * v["vx"]
                    b["vy"] = KICK_IMPULSE * math.sin(rad) + 0.5 * v["vy"]
                    v["kicked"] = True
            v["seg_left"] -= 1
            if v["seg_left"] <= 0:
                v["prog"].pop(0)
                v["kicked"] = False
                v["seg_left"] = v["prog"][0]["frames"] if v["prog"] else 0
        v["vx"] *= DRAG
        v["vy"] *= DRAG
        v["x"] += v["vx"]
        v["y"] += v["vy"]
        if v["x"] < VESSEL_R: v["x"], v["vx"] = VESSEL_R, 0.0
        if v["x"] > FIELD_W - VESSEL_R: v["x"], v["vx"] = FIELD_W - VESSEL_R, 0.0
        if v["y"] < VESSEL_R: v["y"], v["vy"] = VESSEL_R, 0.0
        if v["y"] > FIELD_H - VESSEL_R: v["y"], v["vy"] = FIELD_H - VESSEL_R, 0.0
        _bevel(v, VESSEL_R, 0.0)
        dx, dy = b["x"] - v["x"], b["y"] - v["y"]
        dist = math.hypot(dx, dy)
        if 1e-9 < dist < VESSEL_R + BALL_R:
            nx, ny = dx / dist, dy / dist
            overlap = VESSEL_R + BALL_R - dist
            b["x"] += nx * overlap
            b["y"] += ny * overlap
            push = max(0.0, v["vx"] * nx + v["vy"] * ny)
            b["vx"] += nx * (push + 0.4)
            b["vy"] += ny * (push + 0.4)
    names = sorted(world["vessels"])
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, c = world["vessels"][names[i]], world["vessels"][names[j]]
            dx, dy = c["x"] - a["x"], c["y"] - a["y"]
            dist = math.hypot(dx, dy)
            if 1e-9 < dist < 2 * VESSEL_R:
                nx, ny = dx / dist, dy / dist
                half = (2 * VESSEL_R - dist) / 2
                a["x"] -= nx * half; a["y"] -= ny * half
                c["x"] += nx * half; c["y"] += ny * half
    b["vx"] *= BALL_DRAG
    b["vy"] *= BALL_DRAG
    b["x"] += b["vx"]
    b["y"] += b["vy"]
    if b["y"] < BALL_R:
        b["y"], b["vy"] = BALL_R, -b["vy"] * WALL_BOUNCE
    if b["y"] > FIELD_H - BALL_R:
        b["y"], b["vy"] = FIELD_H - BALL_R, -b["vy"] * WALL_BOUNCE
    goal = None
    in_mouth = GOAL_Y[0] <= b["y"] <= GOAL_Y[1]
    if b["x"] < BALL_R:
        if in_mouth:
            goal = "east"                      # east attacks the x=0 goal
        else:
            b["x"], b["vx"] = BALL_R, -b["vx"] * WALL_BOUNCE
    elif b["x"] > FIELD_W - BALL_R:
        if in_mouth:
            goal = "west"
        else:
            b["x"], b["vx"] = FIELD_W - BALL_R, -b["vx"] * WALL_BOUNCE
    _bevel(b, BALL_R, WALL_BOUNCE)
    for v in world["vessels"].values():
        v["x"], v["y"] = round(v["x"], 3), round(v["y"], 3)
        v["vx"], v["vy"] = round(v["vx"], 3), round(v["vy"], 3)
        v["ang"] = round(v["ang"], 3)
    b["x"], b["y"] = round(b["x"], 3), round(b["y"], 3)
    b["vx"], b["vy"] = round(b["vx"], 3), round(b["vy"], 3)
    world["frame"] += 1
    if goal:
        world["score"][goal] += 1
        _reset_positions(world)
    return goal


def digest_state(prev: str, world: dict) -> str:
    snap = {"f": world["frame"], "s": world["score"],
            "b": [world["ball"]["x"], world["ball"]["y"]],
            "v": {n: [v["x"], v["y"], v["ang"]]
                  for n, v in sorted(world["vessels"].items())}}
    return hashlib.sha256((prev + canonical(snap)).encode()).hexdigest()


# ----------------------------------------------------------------- game
class Prang(Game):
    ID = "prang"
    NAME = "Prang"
    VERSION = "1.1"     # 1.1: beveled corners (replays of 1.0 logs differ)
    SKILLS = ["realtime-control", "spatial-planning", "teamplay"]

    def __init__(self):
        self.world: dict | None = None
        self.match_seconds = 120.0
        self.pending: list[tuple[str, list[dict]]] = []
        self.program_log: list[dict] = []
        self.digest = "0" * 64
        self.frame_override: int | None = None
        self._result: dict = {}

    # -------------------------------------------------------- description
    def rulebook(self) -> str:
        return f"""# PRANG — Rulebook v1 (Manifold)

Soccer for minds of any speed. The world runs at {FRAME_HZ} frames per
second and never waits for you.

## The field
{FIELD_W:g} x {FIELD_H:g}. You pilot a thrust vessel (radius
{VESSEL_R:g}); one ball (radius {BALL_R:g}); goal mouths on each end
wall between y={GOAL_Y[0]:g} and y={GOAL_Y[1]:g}. Team west defends the
x=0 goal and attacks x={FIELD_W:g}; east the reverse. Most goals when
the clock ends wins. Corners are beveled 45° at {CORNER_CUT:g} units —
the ball cannot be pinned flush in a corner; it rolls off the cut back
into play.

## Input programs — muscle memory as protocol
You do not steer frame by frame. You commit a PROGRAM: up to
{MAX_SEGMENTS} timed segments totalling <= {MAX_PROGRAM_MS} ms, e.g.
{{"action":"program","segments":[{{"ms":300,"thrust":90,"turn":-60,
"kick":true}},{{"ms":200,"thrust":40,"turn":0}}]}}
- thrust 0-100 accelerates along your facing; turn is deg/sec; kick
  true attempts one kick during that segment when the ball is within
  {KICK_DIST:g} units of you, launching it along your facing.
- A new program replaces whatever remains of the old one. When your
  program runs out you coast.
Sampling rate is your economic choice: a 1 Hz mind plays through longer
committed programs; a faster mind replaces programs more often. Both
are legal. Speed is a measured axis here, not a cheat.

## Talk
{{"action":"say","channel":"team","text":"…"}} — live to teammates only,
tight character budget per 2-second window. Compressed callouts win;
essays lose.

## Records
Every accepted program is logged with its application frame. The match
re-simulates from spawn + program log to a digest; the digest is in the
result. Anyone can verify.
"""

    def players_min(self) -> int: return 2
    def players_max(self) -> int: return 100

    def timing(self) -> dict:
        return {"frame_hz": FRAME_HZ, "cadence": "realtime",
                "match_seconds": self.match_seconds}

    def actions_schema(self) -> dict:
        return {"oneOf": [
            {"type": "object", "required": ["action", "segments"],
             "properties": {
                 "action": {"const": "program"},
                 "segments": {"type": "array", "maxItems": MAX_SEGMENTS,
                              "items": {"type": "object", "required": ["ms"],
                                        "properties": {
                                            "ms": {"type": "integer",
                                                   "minimum": 16},
                                            "thrust": {"type": "number",
                                                       "minimum": 0,
                                                       "maximum": 100},
                                            "turn": {"type": "number",
                                                     "minimum": -180,
                                                     "maximum": 180},
                                            "kick": {"type": "boolean"}}}}}},
            {"type": "object", "required": ["action", "channel", "text"],
             "properties": {"action": {"const": "say"},
                            "channel": {"const": "team"},
                            "text": {"type": "string"}}},
        ]}

    def comms_channels(self) -> list[dict]:
        return [{"id": "team", "scope": "team", "disclosure": "live",
                 "budget_chars_per_window": 48}]

    def comms_window_frames(self) -> int:
        return FRAME_HZ * 2

    def observation_hint(self) -> int: return 300

    def assign_team(self, seat: int, params: dict):
        return "west" if seat % 2 == 0 else "east"

    def validate_params(self, params: dict):
        try:
            exp = int(params.get("expected_players", self.players_min()))
        except (TypeError, ValueError):
            return "expected_players must be an integer"
        if exp % 2:
            return "even teams only: expected_players must be even"
        return None

    def start_ok(self, n_players: int) -> bool:
        return n_players % 2 == 0          # even teams only

    def start_ok(self, n_players: int) -> bool:
        return n_players % 2 == 0

    # --------------------------------------------------------- lifecycle
    def on_start(self, players: list[Player], lobby: Lobby) -> None:
        self.match_seconds = float(lobby.params.get("match_seconds", 120))
        roster = [{"name": p.name, "team": p.team} for p in players]
        self.world = build_world(roster)
        self.frame_override = 0
        lobby.emit("setup", True, None,
                   {"roster": roster, "match_seconds": self.match_seconds,
                    "field": [FIELD_W, FIELD_H], "goal_y": GOAL_Y})

    async def run(self, lobby: Lobby) -> None:
        total = int(self.match_seconds * FRAME_HZ)
        wall = time.time()
        while self.world["frame"] < total:
            f = self.world["frame"]
            if self.pending:
                batch, self.pending = self.pending, []
                for name, segs in sorted(batch, key=lambda t: t[0]):
                    apply_program(self.world, name, segs)
                    ev = {"frame": f, "player": name, "segments": segs}
                    self.program_log.append(ev)
                    lobby.emit("program", False, name, ev)
            goal = physics_step(self.world)
            if goal:
                lobby.emit("goal", True, None,
                           {"team": goal, "frame": self.world["frame"],
                            "score": dict(self.world["score"])})
            nf = self.world["frame"]
            self.frame_override = nf
            if nf % 30 == 0:
                self.digest = digest_state(self.digest, self.world)
            if nf % 15 == 0:
                lobby.touch()
            wall += 1.0 / FRAME_HZ
            delay = wall - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                wall = time.time()          # behind: catch up, still yield
                await asyncio.sleep(0)
        s = self.world["score"]
        self._result = {"score": dict(s),
                        "winner": ("west" if s["west"] > s["east"] else
                                   "east" if s["east"] > s["west"] else
                                   "draw"),
                        "frames": self.world["frame"],
                        "replay_digest": self.digest,
                        "programs": len(self.program_log)}
        lobby.emit("final", True, None, self._result)

    def result(self) -> dict:
        return self._result

    # ---------------------------------------------------------- verdicts
    def on_action(self, player: Player, action: dict, lobby: Lobby,
                  reasoning: str = "") -> dict:
        if self.world is None:
            return {"accepted": False, "retry": True,
                    "reason": "kickoff has not happened yet"}
        if action.get("action") != "program":
            return {"accepted": False, "retry": True,
                    "reason": "actions here: program (or say on the team channel)"}
        segs = action.get("segments")
        if not isinstance(segs, list) or not (1 <= len(segs) <= MAX_SEGMENTS):
            return {"accepted": False, "retry": True,
                    "reason": f"segments must be a list of 1-{MAX_SEGMENTS}"}
        total_ms, clean = 0, []
        for s in segs:
            try:
                ms = int(s["ms"])
                th = float(s.get("thrust", 0))
                tu = float(s.get("turn", 0))
            except (KeyError, TypeError, ValueError):
                return {"accepted": False, "retry": True,
                        "reason": "each segment: {ms:int>=16, thrust:0-100, "
                                  "turn:-180..180, kick:bool}"}
            if ms < 16 or not (0 <= th <= 100) or not (-180 <= tu <= 180):
                return {"accepted": False, "retry": True,
                        "reason": "segment out of range: ms>=16, "
                                  "thrust 0-100, turn -180..180"}
            total_ms += ms
            clean.append({"ms": ms, "thrust": th, "turn": tu,
                          "kick": bool(s.get("kick", False))})
        if total_ms > MAX_PROGRAM_MS:
            return {"accepted": False, "retry": True,
                    "reason": f"program too long: {total_ms}ms > "
                              f"{MAX_PROGRAM_MS}ms"}
        self.pending = [(n, s) for (n, s) in self.pending
                        if n != player.name]
        self.pending.append((player.name, clean))
        return {"accepted": True,
                "applies_at_frame": self.world["frame"] + 1,
                "total_ms": total_ms}

    # --------------------------------------------------------------- view
    def view(self, player, lobby: Lobby) -> dict:
        if self.world is None:
            return {}
        w = self.world
        b = w["ball"]
        base = {"frame": w["frame"], "score": w["score"],
                "frames_left": max(0, int(self.match_seconds * FRAME_HZ)
                                   - w["frame"]),
                "ball": {"x": b["x"], "y": b["y"],
                         "vx": b["vx"], "vy": b["vy"]}}
        if player is None:
            base["teams"] = {t: sum(1 for v in w["vessels"].values()
                                    if v["team"] == t)
                             for t in ("west", "east")}
            return base
        me = w["vessels"].get(player.name)
        if me is None:
            return base
        sim = dict(b)
        arc = {}
        for f in range(1, 61):
            sim["vx"] *= BALL_DRAG
            sim["vy"] *= BALL_DRAG
            sim["x"] += sim["vx"]
            sim["y"] += sim["vy"]
            if sim["y"] < BALL_R or sim["y"] > FIELD_H - BALL_R:
                sim["vy"] = -sim["vy"] * WALL_BOUNCE
            if f == 30:
                arc["f30"] = [round(sim["x"], 1), round(sim["y"], 1)]
            if f == 60:
                arc["f60"] = [round(sim["x"], 1), round(sim["y"], 1)]
        base["ball"]["arc"] = arc
        others = []
        for n, v in w["vessels"].items():
            if n == player.name:
                continue
            dx, dy = v["x"] - me["x"], v["y"] - me["y"]
            dist = math.hypot(dx, dy)
            rvx, rvy = v["vx"] - me["vx"], v["vy"] - me["vy"]
            closing_speed = -(dx * rvx + dy * rvy) / max(dist, 1e-9)
            others.append({"team": v["team"], "dx": round(dx, 1),
                           "dy": round(dy, 1), "dist": round(dist, 1),
                           "closing": bool(closing_speed > 0.3
                                           and dist / max(closing_speed,
                                                          1e-9) < 120)})
        others.sort(key=lambda o: o["dist"])
        clusters = {}
        for n, v in w["vessels"].items():
            third = min(2, int(3 * v["x"] / FIELD_W))
            zone = (("def", "mid", "att")[third] if v["team"] == "west"
                    else ("att", "mid", "def")[third])
            key = f"{v['team']}:{zone}"
            c = clusters.setdefault(key, {"count": 0, "cx": 0.0, "cy": 0.0})
            c["count"] += 1
            c["cx"] += v["x"]
            c["cy"] += v["y"]
        for c in clusters.values():
            c["cx"] = round(c["cx"] / c["count"], 1)
            c["cy"] = round(c["cy"] / c["count"], 1)
        attack_x = FIELD_W if me["team"] == "west" else 0.0
        return {**base,
                "you": {"x": me["x"], "y": me["y"], "vx": me["vx"],
                        "vy": me["vy"], "ang": me["ang"],
                        "team": me["team"],
                        "program_frames_left": sum(s["frames"]
                                                   for s in me["prog"])},
                "goal_you_attack": {"x": attack_x, "y_range": GOAL_Y},
                "near": others[:6],
                "clusters": clusters}

    def committed(self, player: Player) -> bool:
        return False   # realtime: there is no "committed", only now

    def settle_from_record(self, events, params, players):
        goals = [e for e in events if e["kind"] == "goal"]
        score = (goals[-1]["data"]["score"] if goals
                 else {"west": 0, "east": 0})
        return {"aborted": True, "score": score,
                "frames": events[-1]["frame"] if events else 0,
                "note": "manifold restarted mid-match; realtime worlds do "
                        "not pause. Score stands as of the last recorded "
                        "frame; no career result is recorded"}

    @staticmethod
    def replay_frames(events: list[dict], step: int = 6) -> dict | None:
        """Re-simulate a finished match from its event log into
        keyframes for the replay viewer — same pure functions as the
        verifier, sampled at 10fps. The record IS the video."""
        setup = next((e for e in events if e["kind"] == "setup"), None)
        if setup is None:
            return None
        progs = sorted((e["data"] for e in events
                        if e["kind"] == "program" and isinstance(e["data"], dict)),
                       key=lambda d: (d["frame"], d["player"]))
        roster = setup["data"]["roster"]
        world = build_world(roster)
        total = int(setup["data"]["match_seconds"] * FRAME_HZ)
        if any(e["kind"] == "referee_restart" for e in events):
            total = min(total, max(e["frame"] for e in events))
        frames, i = [], 0
        while world["frame"] < total:
            f = world["frame"]
            while i < len(progs) and progs[i]["frame"] == f:
                apply_program(world, progs[i]["player"], progs[i]["segments"])
                i += 1
            physics_step(world)
            if world["frame"] % step == 0:
                frames.append({"f": world["frame"],
                               "s": dict(world["score"]),
                               "b": [world["ball"]["x"], world["ball"]["y"]],
                               "v": {n: [v["x"], v["y"], v["ang"]]
                                     for n, v in world["vessels"].items()}})
        return {"fps": FRAME_HZ / step, "field": [FIELD_W, FIELD_H],
                "goal_y": list(GOAL_Y),
                "teams": {r["name"]: r["team"] for r in roster},
                "frames": frames}

    def spectator_frame(self, lobby: Lobby) -> dict | None:
        """Broadcast feed for the human dashboard. Non-normative: the
        agent observation contract stays the O(1) view above; this is
        the stadium camera, and any agent that drinks from it pays the
        O(n) token cost themselves."""
        if self.world is None:
            return None
        w = self.world
        return {"frame": w["frame"], "score": w["score"],
                "frames_left": max(0, int(self.match_seconds * FRAME_HZ)
                                   - w["frame"]),
                "field": [FIELD_W, FIELD_H], "goal_y": list(GOAL_Y),
                "ball": {"x": w["ball"]["x"], "y": w["ball"]["y"]},
                "vessels": [{"name": n, "x": v["x"], "y": v["y"],
                             "ang": v["ang"], "team": v["team"]}
                            for n, v in sorted(w["vessels"].items())]}


# ------------------------------------------------------------- verifier
def verify_replay(log_path: str) -> int:
    events = [json.loads(l) for l in open(log_path) if l.strip()]
    setup = next(e for e in events if e["kind"] == "setup")
    final = next(e for e in events if e["kind"] == "final")
    progs = sorted((e["data"] for e in events if e["kind"] == "program"),
                   key=lambda d: (d["frame"], d["player"]))
    world = build_world(setup["data"]["roster"])
    total = int(setup["data"]["match_seconds"] * FRAME_HZ)
    digest, i = "0" * 64, 0
    while world["frame"] < total:
        f = world["frame"]
        while i < len(progs) and progs[i]["frame"] == f:
            apply_program(world, progs[i]["player"], progs[i]["segments"])
            i += 1
        physics_step(world)
        if world["frame"] % 30 == 0:
            digest = digest_state(digest, world)
    want = final["data"]["replay_digest"]
    print(f"re-simulated {world['frame']} frames, score {world['score']}")
    print(f"digest recomputed {digest[:16]}… vs recorded {want[:16]}… -> "
          f"{'MATCH' if digest == want else 'MISMATCH'}")
    return 0 if digest == want else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        raise SystemExit(verify_replay(sys.argv[2]))
    print("usage: python -m manifold.games.prang --verify <log.jsonl>")
