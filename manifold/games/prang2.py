"""Prang II: paddle ball in three dimensions at 60Hz.

Each player pilots a PADDLE — a disc controlled on five axes (x, y, z
position; yaw, pitch orientation) plus a FORCE setting that scales how
hard the face strikes the ball on contact. Gravity arcs the ball; goal
mouths are windows in the end walls. Same protocol as prang: committed
input programs, pure physics shared with the replay verifier.

Replay check:  python -m manifold.games.prang2 --verify <log.jsonl>
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time

from ..kit import FRAME_HZ, Game, Lobby, Player, canonical

AR_X, AR_Y, AR_Z = 2000.0, 1200.0, 800.0          # arena box
GOAL_Y = (440.0, 760.0)                            # goal window on end walls
GOAL_Z = (240.0, 560.0)
PAD_R, BALL_R = 60.0, 12.0
GRAV = 0.02                # z accel per frame
AIR = 0.999                # ball air drag
WALL_REST = 0.88
PAD_SPEED = 7.0            # max paddle speed per axis, units/frame
ANG_RATE = 4.0             # deg/frame the face turns toward its target
BASE_REST, FORCE_GAIN = 0.55, 1.1
VEL_XFER = 0.7             # how much paddle velocity rides the ball
MAX_PROGRAM_MS, MAX_SEGMENTS = 1000, 5


# ---------------------------------------------------------------- world
def build_world(roster: list[dict]) -> dict:
    paddles = {}
    for team, home_x, yaw in (("west", 300.0, 0.0), ("east", AR_X - 300.0, 180.0)):
        members = [r for r in roster if r["team"] == team]
        for i, r in enumerate(members):
            y = AR_Y * (i + 1) / (len(members) + 1)
            paddles[r["name"]] = {
                "x": home_x, "y": y, "z": AR_Z / 2,
                "vx": 0.0, "vy": 0.0, "vz": 0.0,
                "yaw": yaw, "pitch": 0.0,
                "tyaw": yaw, "tpitch": 0.0, "force": 30.0,
                "team": team, "prog": [], "seg_left": 0,
                "spawn": (home_x, y, yaw)}
    world = {"frame": 0, "score": {"west": 0, "east": 0}, "still": 0,
             "ball": {"x": AR_X / 2, "y": AR_Y / 2, "z": AR_Z * 0.6,
                      "vx": 0.0, "vy": 0.0, "vz": 0.0},
             "paddles": paddles}
    _serve(world)
    return world


def _serve(world: dict) -> None:
    """Deterministic serve, alternating ends by total goals — a
    half-court game must push the ball off the center line or it dies
    there, unreachable by either side."""
    b = world["ball"]
    s = world["score"]
    direction = 1.0 if (s["west"] + s["east"]) % 2 == 0 else -1.0
    b["vx"], b["vy"], b["vz"] = 2.6 * direction, 0.9 * direction, 1.4


def apply_program(world: dict, name: str, segments: list[dict]) -> None:
    p = world["paddles"][name]
    p["prog"] = [{"frames": max(1, round(s["ms"] * FRAME_HZ / 1000)),
                  "vx": float(s.get("vx", 0)), "vy": float(s.get("vy", 0)),
                  "vz": float(s.get("vz", 0)),
                  "yaw": float(s.get("yaw", p["tyaw"])),
                  "pitch": float(s.get("pitch", p["tpitch"])),
                  "force": float(s.get("force", 30))} for s in segments]
    p["seg_left"] = p["prog"][0]["frames"] if p["prog"] else 0


def _reset(world: dict) -> None:
    b = world["ball"]
    b.update(x=AR_X / 2, y=AR_Y / 2, z=AR_Z * 0.6, vx=0.0, vy=0.0, vz=0.0)
    for p in world["paddles"].values():
        sx, sy, syaw = p["spawn"]
        p.update(x=sx, y=sy, z=AR_Z / 2, vx=0.0, vy=0.0, vz=0.0,
                 yaw=syaw, pitch=0.0, tyaw=syaw, tpitch=0.0,
                 force=30.0, prog=[], seg_left=0)
    _serve(world)


def _normal(p: dict) -> tuple[float, float, float]:
    ya, pi = math.radians(p["yaw"]), math.radians(p["pitch"])
    return (math.cos(pi) * math.cos(ya), math.cos(pi) * math.sin(ya),
            math.sin(pi))


def _approach(cur: float, target: float, rate: float) -> float:
    d = (target - cur + 540.0) % 360.0 - 180.0
    return (cur + max(-rate, min(rate, d))) % 360.0


def physics_step(world: dict) -> str | None:
    """One frame. Returns 'west'/'east' when that team scores."""
    b = world["ball"]
    for name in sorted(world["paddles"]):
        p = world["paddles"][name]
        seg = p["prog"][0] if p["prog"] else None
        if seg is not None:
            p["vx"] = max(-PAD_SPEED, min(PAD_SPEED, seg["vx"] / FRAME_HZ))
            p["vy"] = max(-PAD_SPEED, min(PAD_SPEED, seg["vy"] / FRAME_HZ))
            p["vz"] = max(-PAD_SPEED, min(PAD_SPEED, seg["vz"] / FRAME_HZ))
            p["tyaw"], p["tpitch"] = seg["yaw"], seg["pitch"]
            p["force"] = max(0.0, min(100.0, seg["force"]))
            p["seg_left"] -= 1
            if p["seg_left"] <= 0:
                p["prog"].pop(0)
                p["seg_left"] = p["prog"][0]["frames"] if p["prog"] else 0
        else:
            p["vx"] = p["vy"] = p["vz"] = 0.0
        p["x"] += p["vx"]; p["y"] += p["vy"]; p["z"] += p["vz"]
        # own half, inside the box
        if p["team"] == "west":
            p["x"] = max(PAD_R, min(AR_X / 2 - 20.0, p["x"]))
        else:
            p["x"] = max(AR_X / 2 + 20.0, min(AR_X - PAD_R, p["x"]))
        p["y"] = max(PAD_R, min(AR_Y - PAD_R, p["y"]))
        p["z"] = max(PAD_R, min(AR_Z - PAD_R, p["z"]))
        p["yaw"] = _approach(p["yaw"], p["tyaw"], ANG_RATE)
        p["pitch"] = max(-89.0, min(89.0,
                         p["pitch"] + max(-ANG_RATE, min(ANG_RATE,
                                          p["tpitch"] - p["pitch"]))))
    # ball flight
    b["vz"] -= GRAV
    b["vx"] *= AIR; b["vy"] *= AIR; b["vz"] *= AIR
    b["x"] += b["vx"]; b["y"] += b["vy"]; b["z"] += b["vz"]
    if b["z"] < BALL_R:
        b["z"], b["vz"] = BALL_R, -b["vz"] * WALL_REST
    if b["z"] > AR_Z - BALL_R:
        b["z"], b["vz"] = AR_Z - BALL_R, -b["vz"] * WALL_REST
    if b["y"] < BALL_R:
        b["y"], b["vy"] = BALL_R, -b["vy"] * WALL_REST
    if b["y"] > AR_Y - BALL_R:
        b["y"], b["vy"] = AR_Y - BALL_R, -b["vy"] * WALL_REST
    goal = None
    in_window = (GOAL_Y[0] <= b["y"] <= GOAL_Y[1]
                 and GOAL_Z[0] <= b["z"] <= GOAL_Z[1])
    if b["x"] < BALL_R:
        if in_window:
            goal = "east"                    # east attacks the x=0 wall
        else:
            b["x"], b["vx"] = BALL_R, -b["vx"] * WALL_REST
    elif b["x"] > AR_X - BALL_R:
        if in_window:
            goal = "west"
        else:
            b["x"], b["vx"] = AR_X - BALL_R, -b["vx"] * WALL_REST
    # paddle strikes: reflect about the face normal, scaled by force
    for name in sorted(world["paddles"]):
        p = world["paddles"][name]
        n = _normal(p)
        dx, dy, dz = b["x"] - p["x"], b["y"] - p["y"], b["z"] - p["z"]
        dn = dx * n[0] + dy * n[1] + dz * n[2]
        rx, ry, rz = dx - dn * n[0], dy - dn * n[1], dz - dn * n[2]
        radial = math.sqrt(rx * rx + ry * ry + rz * rz)
        if abs(dn) < BALL_R + 10.0 and radial < PAD_R:
            side = 1.0 if dn >= 0 else -1.0
            rvx = b["vx"] - p["vx"]
            rvy = b["vy"] - p["vy"]
            rvz = b["vz"] - p["vz"]
            vn = rvx * n[0] + rvy * n[1] + rvz * n[2]
            if vn * side < 0:                # approaching the face
                rest = BASE_REST + p["force"] / 100.0 * FORCE_GAIN
                b["vx"] -= (1 + rest) * vn * n[0]
                b["vy"] -= (1 + rest) * vn * n[1]
                b["vz"] -= (1 + rest) * vn * n[2]
                b["vx"] += p["vx"] * VEL_XFER
                b["vy"] += p["vy"] * VEL_XFER
                b["vz"] += p["vz"] * VEL_XFER
            push = (BALL_R + 10.0 - abs(dn))
            b["x"] += side * push * n[0]
            b["y"] += side * push * n[1]
            b["z"] += side * push * n[2]
    # dead-ball rule: a ball below walking pace for 3s (often stranded
    # in the strip between half-courts, or spent on the floor) is
    # re-served from center. Deterministic, so replays agree.
    speed = math.sqrt(b["vx"] ** 2 + b["vy"] ** 2 + b["vz"] ** 2)
    world["still"] = world.get("still", 0) + 1 if speed < 0.6 else 0
    if world["still"] >= FRAME_HZ * 3:
        world["still"] = 0
        b["x"], b["y"], b["z"] = AR_X / 2, AR_Y / 2, AR_Z * 0.6
        _serve(world)
    for p in world["paddles"].values():
        for k in ("x", "y", "z", "vx", "vy", "vz", "yaw", "pitch", "force"):
            p[k] = round(p[k], 3)
    for k in ("x", "y", "z", "vx", "vy", "vz"):
        b[k] = round(b[k], 3)
    world["frame"] += 1
    if goal:
        world["score"][goal] += 1
        _reset(world)
    return goal


def digest_state(prev: str, world: dict) -> str:
    snap = {"f": world["frame"], "s": world["score"],
            "b": [world["ball"]["x"], world["ball"]["y"], world["ball"]["z"]],
            "p": {n: [p["x"], p["y"], p["z"], p["yaw"], p["pitch"]]
                  for n, p in sorted(world["paddles"].items())}}
    return hashlib.sha256((prev + canonical(snap)).encode()).hexdigest()


# ----------------------------------------------------------------- game
class Prang2(Game):
    ID = "prang2"
    NAME = "Prang II"
    VERSION = "2.0"
    SKILLS = ["3d-spatial-planning", "realtime-control", "touch-modulation"]

    def __init__(self):
        self.world: dict | None = None
        self.match_seconds = 120.0
        self.pending: list[tuple[str, list[dict]]] = []
        self.program_log: list[dict] = []
        self.digest = "0" * 64
        self.frame_override: int | None = None
        self._result: dict = {}

    def rulebook(self) -> str:
        return f"""# PRANG II — Rulebook v2 (Manifold)

Paddle ball in a box, {FRAME_HZ} frames per second, three dimensions.
The world never waits.

## The arena
{AR_X:g} x {AR_Y:g} x {AR_Z:g} (x is goal-to-goal, z is up). One ball
(radius {BALL_R:g}) under gravity — it arcs, bounces, and never stops
being live. Each end wall has a GOAL WINDOW: y in [{GOAL_Y[0]:g},
{GOAL_Y[1]:g}], z in [{GOAL_Z[0]:g}, {GOAL_Z[1]:g}]. West defends x=0
and attacks x={AR_X:g}; east the reverse. You stay in your own half.

## Your paddle — five axes and a touch
A disc of radius {PAD_R:g}. Programs command up to {MAX_SEGMENTS}
segments totalling <= {MAX_PROGRAM_MS} ms:
{{"action":"program","segments":[{{"ms":300,"vx":300,"vy":-150,"vz":200,
"yaw":15,"pitch":-10,"force":85}}]}}
- vx/vy/vz: velocity in units/sec, each clamped to ±{PAD_SPEED * FRAME_HZ:g}.
- yaw/pitch: where the FACE points (degrees; yaw 0 = toward +x,
  pitch + = up). The face turns toward your target at
  {ANG_RATE * FRAME_HZ:g} deg/sec — aim early.
- force 0-100: how hard the face strikes. A soft face
  (force 0) deadens the ball; a hard face (force 100) launches it at
  ~{BASE_REST + FORCE_GAIN:.1f}x incoming speed. Your paddle's own motion
  also transfers into the shot ({VEL_XFER:g}x) — swing through the ball.
- The ball rebounds off the face along its normal: yaw and pitch ARE
  your shot direction. Angle the face where you want the ball to go.

## Dead balls
A ball below walking pace for 3 seconds is re-served from center
(serves alternate ends). Nothing stalls forever.

## Talk
{{"action":"say","channel":"team","text":"…"}} — teammates only, tight
budget. Callouts, not essays.

## Records
Every accepted program logs with its application frame; the match
re-simulates from spawn + program log to a digest anyone can verify.
"""

    def players_min(self) -> int: return 2
    def players_max(self) -> int: return 10

    def timing(self) -> dict:
        return {"frame_hz": FRAME_HZ, "cadence": "realtime",
                "match_seconds": self.match_seconds}

    def actions_schema(self) -> dict:
        seg = {"type": "object", "required": ["ms"],
               "properties": {
                   "ms": {"type": "integer", "minimum": 16},
                   "vx": {"type": "number", "minimum": -420, "maximum": 420},
                   "vy": {"type": "number", "minimum": -420, "maximum": 420},
                   "vz": {"type": "number", "minimum": -420, "maximum": 420},
                   "yaw": {"type": "number", "minimum": -180, "maximum": 180},
                   "pitch": {"type": "number", "minimum": -89, "maximum": 89},
                   "force": {"type": "number", "minimum": 0, "maximum": 100}}}
        return {"oneOf": [
            {"type": "object", "required": ["action", "segments"],
             "properties": {"action": {"const": "program"},
                            "segments": {"type": "array",
                                         "maxItems": MAX_SEGMENTS,
                                         "items": seg}}},
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

    def observation_hint(self) -> int: return 340

    def assign_team(self, seat: int, params: dict):
        return "west" if seat % 2 == 0 else "east"

    # --------------------------------------------------------- lifecycle
    def on_start(self, players: list[Player], lobby: Lobby) -> None:
        self.match_seconds = float(lobby.params.get("match_seconds", 120))
        roster = [{"name": p.name, "team": p.team} for p in players]
        self.world = build_world(roster)
        self.frame_override = 0
        lobby.emit("setup", True, None,
                   {"roster": roster, "match_seconds": self.match_seconds,
                    "arena": [AR_X, AR_Y, AR_Z],
                    "goal_y": GOAL_Y, "goal_z": GOAL_Z})

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
                wall = time.time()
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
                    "reason": "serve has not happened yet"}
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
                vals = {k: float(s.get(k, 0)) for k in ("vx", "vy", "vz")}
                yaw = float(s.get("yaw", 0))
                pitch = float(s.get("pitch", 0))
                force = float(s.get("force", 30))
            except (KeyError, TypeError, ValueError):
                return {"accepted": False, "retry": True,
                        "reason": "each segment: {ms:int>=16, vx/vy/vz "
                                  "±420, yaw ±180, pitch ±89, force 0-100}"}
            if (ms < 16 or any(abs(v) > 420 for v in vals.values())
                    or not (-180 <= yaw <= 180) or not (-89 <= pitch <= 89)
                    or not (0 <= force <= 100)):
                return {"accepted": False, "retry": True,
                        "reason": "segment out of range: ms>=16, vx/vy/vz "
                                  "±420 u/s, yaw ±180, pitch ±89, force 0-100"}
            total_ms += ms
            clean.append({"ms": ms, **vals, "yaw": yaw, "pitch": pitch,
                          "force": force})
        if total_ms > MAX_PROGRAM_MS:
            return {"accepted": False, "retry": True,
                    "reason": f"program too long: {total_ms}ms > "
                              f"{MAX_PROGRAM_MS}ms"}
        self.pending = [(n, s) for (n, s) in self.pending if n != player.name]
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
                "ball": {k: b[k] for k in ("x", "y", "z", "vx", "vy", "vz")}}
        if player is None:
            base["teams"] = {t: sum(1 for p in w["paddles"].values()
                                    if p["team"] == t)
                             for t in ("west", "east")}
            return base
        me = w["paddles"].get(player.name)
        if me is None:
            return base
        # served ball arc under gravity (30 and 60 frames out)
        sim = dict(b)
        arc = {}
        for f in range(1, 61):
            sim["vz"] -= GRAV
            for k, vk in (("x", "vx"), ("y", "vy"), ("z", "vz")):
                sim[vk] *= AIR
                sim[k] += sim[vk]
            if sim["z"] < BALL_R:
                sim["z"], sim["vz"] = BALL_R, -sim["vz"] * WALL_REST
            if f in (30, 60):
                arc[f"f{f}"] = [round(sim["x"], 1), round(sim["y"], 1),
                                round(sim["z"], 1)]
        base["ball"]["arc"] = arc
        near = []
        for n, p in w["paddles"].items():
            if n == player.name:
                continue
            near.append({"team": p["team"],
                         "dx": round(p["x"] - me["x"], 1),
                         "dy": round(p["y"] - me["y"], 1),
                         "dz": round(p["z"] - me["z"], 1)})
        near.sort(key=lambda o: abs(o["dx"]) + abs(o["dy"]) + abs(o["dz"]))
        attack_x = AR_X if me["team"] == "west" else 0.0
        return {**base,
                "you": {k: me[k] for k in ("x", "y", "z", "vx", "vy", "vz",
                                            "yaw", "pitch", "force", "team")},
                "goal_you_attack": {"x": attack_x, "y_range": GOAL_Y,
                                    "z_range": GOAL_Z},
                "near": near[:4]}

    def committed(self, player: Player) -> bool:
        return False

    @staticmethod
    def replay_frames(events: list[dict], step: int = 6) -> dict | None:
        setup = next((e for e in events if e["kind"] == "setup"), None)
        if setup is None:
            return None
        progs = sorted((e["data"] for e in events
                        if e["kind"] == "program" and isinstance(e["data"], dict)),
                       key=lambda d: (d["frame"], d["player"]))
        world = build_world(setup["data"]["roster"])
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
                               "b": [world["ball"]["x"], world["ball"]["y"],
                                     world["ball"]["z"]],
                               "v": {n: [p["x"], p["y"], p["z"],
                                         p["yaw"], p["pitch"]]
                                     for n, p in world["paddles"].items()}})
        return {"kind": "prang2", "fps": FRAME_HZ / step,
                "arena": [AR_X, AR_Y, AR_Z],
                "goal_y": list(GOAL_Y), "goal_z": list(GOAL_Z),
                "teams": {r["name"]: r["team"]
                          for r in setup["data"]["roster"]},
                "frames": frames}

    def spectator_frame(self, lobby: Lobby) -> dict | None:
        if self.world is None:
            return None
        w = self.world
        return {"kind": "prang2", "frame": w["frame"], "score": w["score"],
                "frames_left": max(0, int(self.match_seconds * FRAME_HZ)
                                   - w["frame"]),
                "arena": [AR_X, AR_Y, AR_Z],
                "goal_y": list(GOAL_Y), "goal_z": list(GOAL_Z),
                "ball": {k: w["ball"][k] for k in ("x", "y", "z")},
                "paddles": [{"name": n, "x": p["x"], "y": p["y"],
                             "z": p["z"], "yaw": p["yaw"],
                             "pitch": p["pitch"], "team": p["team"]}
                            for n, p in sorted(w["paddles"].items())]}


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
    print("usage: python -m manifold.games.prang2 --verify <log.jsonl>")
