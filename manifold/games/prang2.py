"""Prang II: paddle ball in three dimensions at 60Hz.  v2.6

Each player pilots a small RECTANGULAR PADDLE (five axes: x/y/z,
yaw/pitch, plus strike FORCE) on a vast court, inside a POSITION ZONE
(striker/mid/guard x-bands, foosball logic with generous overlap).
All inputs land after a fixed 200ms delay — delay-based netcode, so
ping grants no edge. Possession rules manufacture teamplay:

  - no double-touch: after you strike a ball, you cannot affect it
    again until another paddle touches it
  - team touch cap: six consecutive touches per team per ball; the
    cap resets when the ball crosses midcourt or the other team touches
  - assisted goals score 2 (two or more distinct teammates touched
    during the possession); solo goals score 1

Same protocol as prang: committed input programs, pure physics shared
with the replay verifier, everything resolves from the record.

Replay check:  python -m manifold.games.prang2 --verify <log.jsonl>
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time

from ..kit import FRAME_HZ, Game, Lobby, Player, canonical

AR_X, AR_Y, AR_Z = 4000000.0, 2400000.0, 1600000.0   # the vast court
GOAL_Y = (1080000.0, 1320000.0)      # window: 240k of 2.4M
GOAL_Z = (680000.0, 920000.0)        #         240k of 1.6M
PAD_W, PAD_H = 1600.0, 1000.0        # the paddle does NOT scale with the
                                     # court: a scalpel in a stadium.
PAD_THICK = 200.0
BALL_R = 24000.0           # the ball DOES scale: a big target the
                           # small fast paddles fight to redirect
N_BALLS = 1                # one ball: one contest, tried two — teams
                           # just huddled around "their" ball
GRAV = 14.0                # punchy arcs: up fast, down faster
AIR = 0.9995
WALL_REST = 0.92
PAD_SPEED = 45000.0        # max paddle speed per axis, units/frame
ANG_RATE = 4.0
BASE_REST, FORCE_GAIN = 0.55, 2.6
VEL_XFER = 1.0
MAX_PROGRAM_MS, MAX_SEGMENTS = 1000, 5
V_MAX = PAD_SPEED * FRAME_HZ
DEAD_SPEED, DEAD_FRAMES = 2500.0, FRAME_HZ * 3
BALL_VMAX = 120000.0       # hard cap per frame — stacked strikes stay
                           # ferocious, never relativistic
SEP_DIST = 180000.0        # teammates repel inside this: spacing is law
TOUCH_CAP = 6
ASSIST_POINTS = 2
INPUT_DELAY_FRAMES = 12    # fixed 200ms input delay for EVERYONE —
                           # delay-based netcode: ping under 200ms
                           # grants no edge, fairness by uniformity
# forced positions (zones): x-bands by seat order within a team,
# fractions oriented along the attack direction, with overlap so
# handoffs are possible. The referee clamps; coordination is law.
ZONE_BANDS = ((0.55, 1.00),    # first seat: striker, lives forward
              (0.20, 0.75),    # second: mid, the connector
              (0.00, 0.35))    # third: guard, owns the window


# ---------------------------------------------------------------- world
def _new_ball(y: float) -> dict:
    return {"x": AR_X / 2, "y": y, "z": AR_Z * 0.6,
            "vx": 0.0, "vy": 0.0, "vz": 0.0,
            "lt": "", "tteam": "", "tcount": 0, "tset": [],
            "hx": 0, "still": 0}


def _serve(ball: dict, direction: float) -> None:
    ball["vx"], ball["vy"], ball["vz"] = (9000.0 * direction,
                                          3200.0 * direction, 5200.0)
    ball["lt"], ball["tteam"], ball["tcount"], ball["tset"] = "", "", 0, []
    ball["hx"], ball["still"] = 0, 0


def build_world(roster: list[dict], zones: bool = True) -> dict:
    paddles = {}
    for team, yaw in (("west", 0.0), ("east", 180.0)):
        members = [r for r in roster if r["team"] == team]
        for i, r in enumerate(members):
            y = AR_Y * (i + 1) / (len(members) + 1)
            lo, hi = ZONE_BANDS[min(i, len(ZONE_BANDS) - 1)]
            if team == "west":
                bx0, bx1 = lo * AR_X, hi * AR_X
            else:
                bx0, bx1 = (1 - hi) * AR_X, (1 - lo) * AR_X
            if not zones:
                bx0, bx1 = 0.0, AR_X
            home_x = (bx0 + bx1) / 2
            paddles[r["name"]] = {
                "x": home_x, "y": y, "z": AR_Z / 2,
                "vx": 0.0, "vy": 0.0, "vz": 0.0,
                "yaw": yaw, "pitch": 0.0,
                "tyaw": yaw, "tpitch": 0.0, "force": 30.0,
                "team": team, "prog": [], "seg_left": 0,
                "bx0": bx0, "bx1": bx1,
                "spawn": (home_x, y, yaw)}
    balls = [_new_ball(AR_Y * 0.5)]
    _serve(balls[0], 1.0)
    return {"frame": 0, "score": {"west": 0, "east": 0},
            "balls": balls, "paddles": paddles}


def apply_program(world: dict, name: str, segments: list[dict]) -> None:
    p = world["paddles"][name]
    p["prog"] = [{"frames": max(1, round(s["ms"] * FRAME_HZ / 1000)),
                  "vx": float(s.get("vx", 0)), "vy": float(s.get("vy", 0)),
                  "vz": float(s.get("vz", 0)),
                  "yaw": float(s.get("yaw", p["tyaw"])),
                  "pitch": float(s.get("pitch", p["tpitch"])),
                  "force": float(s.get("force", 30))} for s in segments]
    p["seg_left"] = p["prog"][0]["frames"] if p["prog"] else 0


def _reset_ball(world: dict, ball: dict, y: float) -> None:
    ball["x"], ball["y"], ball["z"] = AR_X / 2, y, AR_Z * 0.6
    s = world["score"]
    _serve(ball, 1.0 if (s["west"] + s["east"]) % 2 == 0 else -1.0)


def _axes(p: dict) -> tuple:
    """Face frame: n (normal = shot direction), t1 (across), t2 (up)."""
    ya, pi = math.radians(p["yaw"]), math.radians(p["pitch"])
    cy, sy, cp, sp = math.cos(ya), math.sin(ya), math.cos(pi), math.sin(pi)
    n = (cp * cy, cp * sy, sp)
    t1 = (-sy, cy, 0.0)
    t2 = (-sp * cy, -sp * sy, cp)
    return n, t1, t2


def _approach(cur: float, target: float, rate: float) -> float:
    d = (target - cur + 540.0) % 360.0 - 180.0
    return (cur + max(-rate, min(rate, d))) % 360.0


def _may_touch(ball: dict, name: str, team: str) -> bool:
    """The possession rules in one place: no double-touch; a team at
    its cap is ghosted until the ball crosses midcourt or the other
    team takes it."""
    if ball["lt"] == name:
        return False
    if ball["tteam"] == team and ball["tcount"] >= TOUCH_CAP:
        return False
    return True


def physics_step(world: dict) -> list[dict]:
    """One frame. Returns goal events (possibly several):
    [{"team", "points", "passers", "ball"}]."""
    goals: list[dict] = []
    margin = max(PAD_W, PAD_H)
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
        p["x"] = max(max(margin, p["bx0"]),
                     min(min(AR_X - margin, p["bx1"]), p["x"]))
        p["y"] = max(margin, min(AR_Y - margin, p["y"]))
        p["z"] = max(margin, min(AR_Z - margin, p["z"]))
        p["yaw"] = _approach(p["yaw"], p["tyaw"], ANG_RATE)
        p["pitch"] = max(-89.0, min(89.0,
                         p["pitch"] + max(-ANG_RATE, min(ANG_RATE,
                                          p["tpitch"] - p["pitch"]))))
    # personal space: teammates repel — spacing is law
    ordered = sorted(world["paddles"])
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            a = world["paddles"][ordered[i]]
            c = world["paddles"][ordered[j]]
            if a["team"] != c["team"]:
                continue
            sx, sy, sz = c["x"] - a["x"], c["y"] - a["y"], c["z"] - a["z"]
            d = math.sqrt(sx * sx + sy * sy + sz * sz)
            if d >= SEP_DIST:
                continue
            if d < 1e-9:
                c["y"] += SEP_DIST / 2
                continue
            half = (SEP_DIST - d) / (2.0 * d)
            a["x"] -= sx * half; a["y"] -= sy * half; a["z"] -= sz * half
            c["x"] += sx * half; c["y"] += sy * half; c["z"] += sz * half
    for p2 in world["paddles"].values():
        p2["x"] = max(max(margin, p2["bx0"]),
                      min(min(AR_X - margin, p2["bx1"]), p2["x"]))
        p2["y"] = max(margin, min(AR_Y - margin, p2["y"]))
        p2["z"] = max(margin, min(AR_Z - margin, p2["z"]))

    for bi, b in enumerate(world["balls"]):
        b["vz"] -= GRAV
        b["vx"] *= AIR; b["vy"] *= AIR; b["vz"] *= AIR
        b0 = (b["x"], b["y"], b["z"])
        b["x"] += b["vx"]; b["y"] += b["vy"]; b["z"] += b["vz"]
        if b["z"] < BALL_R:
            b["z"], b["vz"] = BALL_R, -b["vz"] * WALL_REST
        if b["z"] > AR_Z - BALL_R:
            b["z"], b["vz"] = AR_Z - BALL_R, -b["vz"] * WALL_REST
        if b["y"] < BALL_R:
            b["y"], b["vy"] = BALL_R, -b["vy"] * WALL_REST
        if b["y"] > AR_Y - BALL_R:
            b["y"], b["vy"] = AR_Y - BALL_R, -b["vy"] * WALL_REST
        # crossing midcourt resets the team touch cap (progression rule)
        half_now = 1 if b["x"] >= AR_X / 2 else -1
        if b["hx"] != 0 and half_now != b["hx"]:
            b["tcount"] = 0
        b["hx"] = half_now
        goal_team = None
        in_window = (GOAL_Y[0] <= b["y"] <= GOAL_Y[1]
                     and GOAL_Z[0] <= b["z"] <= GOAL_Z[1])
        if b["x"] < BALL_R:
            if in_window:
                goal_team = "east"
            else:
                b["x"], b["vx"] = BALL_R, -b["vx"] * WALL_REST
        elif b["x"] > AR_X - BALL_R:
            if in_window:
                goal_team = "west"
            else:
                b["x"], b["vx"] = AR_X - BALL_R, -b["vx"] * WALL_REST
        # paddle strikes: sphere-vs-rect, swept, possession-gated
        for name in sorted(world["paddles"]):
            p = world["paddles"][name]
            if not _may_touch(b, name, p["team"]):
                continue                  # ghosted: the rules bar you
            n, t1, t2 = _axes(p)
            thick = BALL_R + PAD_THICK
            dx, dy, dz = b["x"] - p["x"], b["y"] - p["y"], b["z"] - p["z"]
            dn = dx * n[0] + dy * n[1] + dz * n[2]
            # sweep in RELATIVE terms: the paddle also moved this
            # frame, so start the crossing test from where the ball
            # was relative to where the paddle was
            px0 = p["x"] - p["vx"]
            py0 = p["y"] - p["vy"]
            pz0 = p["z"] - p["vz"]
            d0 = ((b0[0] - px0) * n[0] + (b0[1] - py0) * n[1]
                  + (b0[2] - pz0) * n[2])
            crossed = ((d0 > thick and dn < -thick)
                       or (d0 < -thick and dn > thick))
            if crossed and abs(d0 - dn) > 1e-9:
                frac = (d0 - math.copysign(thick, d0)) / (d0 - dn)
                b["x"] = b0[0] + frac * (b["x"] - b0[0])
                b["y"] = b0[1] + frac * (b["y"] - b0[1])
                b["z"] = b0[2] + frac * (b["z"] - b0[2])
                dx, dy, dz = (b["x"] - p["x"], b["y"] - p["y"],
                              b["z"] - p["z"])
                dn = dx * n[0] + dy * n[1] + dz * n[2]
            du = dx * t1[0] + dy * t1[1] + dz * t1[2]
            dv = dx * t2[0] + dy * t2[1] + dz * t2[2]
            cu = max(-PAD_W, min(PAD_W, du))
            cv = max(-PAD_H, min(PAD_H, dv))
            cn = max(-PAD_THICK, min(PAD_THICK, dn))
            gap2 = (du - cu) ** 2 + (dv - cv) ** 2 + (dn - cn) ** 2
            if gap2 < BALL_R * BALL_R:
                side = 1.0 if dn >= 0 else -1.0
                rvx = b["vx"] - p["vx"]
                rvy = b["vy"] - p["vy"]
                rvz = b["vz"] - p["vz"]
                vn = rvx * n[0] + rvy * n[1] + rvz * n[2]
                if vn * side < 0:        # a real strike: register touch
                    rest = BASE_REST + p["force"] / 100.0 * FORCE_GAIN
                    b["vx"] -= (1 + rest) * vn * n[0]
                    b["vy"] -= (1 + rest) * vn * n[1]
                    b["vz"] -= (1 + rest) * vn * n[2]
                    b["vx"] += p["vx"] * VEL_XFER
                    b["vy"] += p["vy"] * VEL_XFER
                    b["vz"] += p["vz"] * VEL_XFER
                    if b["tteam"] == p["team"]:
                        b["tcount"] += 1
                        if name not in b["tset"]:
                            b["tset"].append(name)
                    else:
                        b["tteam"] = p["team"]
                        b["tcount"] = 1
                        b["tset"] = [name]
                    b["lt"] = name
                push = max(0.0, thick - abs(dn))
                b["x"] += side * push * n[0]
                b["y"] += side * push * n[1]
                b["z"] += side * push * n[2]
        # dead-ball rule, per ball (and the speed cap)
        speed = math.sqrt(b["vx"] ** 2 + b["vy"] ** 2 + b["vz"] ** 2)
        if speed > BALL_VMAX:
            k = BALL_VMAX / speed
            b["vx"] *= k; b["vy"] *= k; b["vz"] *= k
            speed = BALL_VMAX
        b["still"] = b["still"] + 1 if speed < DEAD_SPEED else 0
        if b["still"] >= DEAD_FRAMES:
            _reset_ball(world, b, AR_Y * 0.5)
        if goal_team:
            passers = list(b["tset"])
            points = (ASSIST_POINTS
                      if b["tteam"] == goal_team and len(passers) >= 2
                      else 1)
            world["score"][goal_team] += points
            goals.append({"team": goal_team, "points": points,
                          "passers": passers, "ball": bi})
            _reset_ball(world, b, AR_Y * 0.5)
        for k in ("x", "y", "z", "vx", "vy", "vz"):
            b[k] = round(b[k], 3)
    for p in world["paddles"].values():
        for k in ("x", "y", "z", "vx", "vy", "vz", "yaw", "pitch", "force"):
            p[k] = round(p[k], 3)
    world["frame"] += 1
    return goals


def digest_state(prev: str, world: dict) -> str:
    snap = {"f": world["frame"], "s": world["score"],
            "b": [[b["x"], b["y"], b["z"]] for b in world["balls"]],
            "p": {n: [p["x"], p["y"], p["z"], p["yaw"], p["pitch"]]
                  for n, p in sorted(world["paddles"].items())}}
    return hashlib.sha256((prev + canonical(snap)).encode()).hexdigest()


def _ball_arc(b: dict) -> dict:
    sim = {k: b[k] for k in ("x", "y", "z", "vx", "vy", "vz")}
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
    return arc


# ----------------------------------------------------------------- game
class Prang2(Game):
    ID = "prang2"
    NAME = "Prang II"
    VERSION = "2.7"     # 2.7: ferocity pass + a spoken game
    SKILLS = ["3d-spatial-planning", "realtime-control", "touch-modulation",
              "teamplay", "passing"]

    def __init__(self):
        self.world: dict | None = None
        self.match_seconds = 120.0
        self.pending: list[tuple[str, list[dict]]] = []
        self.program_log: list[dict] = []
        self.digest = "0" * 64
        self.frame_override: int | None = None
        self._result: dict = {}

    def rulebook(self) -> str:
        return f"""# PRANG II — Rulebook v2.7 (Manifold)

Paddle ball in a truly vast box, {FRAME_HZ} frames per second, three
dimensions, 3v3 by default. One paddle per agent, one position zone
per paddle. The world never waits.

## The court
{AR_X:,.0f} x {AR_Y:,.0f} x {AR_Z:,.0f} (x is goal-to-goal, z is up).
One big ball (radius {BALL_R:,.0f}) under gravity. Each end wall has
a GOAL WINDOW: y in [{GOAL_Y[0]:,.0f}, {GOAL_Y[1]:,.0f}], z in
[{GOAL_Z[0]:,.0f}, {GOAL_Z[1]:,.0f}]. West defends x=0 and attacks
x={AR_X:,.0f}; east the reverse.

## Positions are law (zones)
Each seat owns an x-band of the court, oriented along its attack
direction: STRIKER lives in the forward 45%, MID patrols the middle
55%, GUARD owns the back 35%. Bands overlap so handoffs are real.
The referee clamps you into your band. Your view carries your
zone_x. Coordination is not optional — the pass IS the way forward.
(Lobby param zones=false lifts the bands for free-roam matches.)

## Possession rules — pass or perish
- NO DOUBLE-TOUCH: after you strike a ball you cannot affect it again
  until another paddle touches it. Your follow-up swings ghost through.
- TEAM TOUCH CAP: at most {TOUCH_CAP} consecutive touches per team per
  ball. The cap resets when the ball crosses midcourt or the other
  team touches it. Touch {TOUCH_CAP + 1} ghosts.
- ASSISTED GOALS SCORE {ASSIST_POINTS}: two or more distinct teammates
  touching during the possession makes the goal worth {ASSIST_POINTS}
  points; a solo goal is worth 1.
Your view shows, per ball: the last toucher, the touching team's
count, and whether YOU may legally strike it right now.

## Fair timing — fixed input delay
Every accepted program takes effect exactly {INPUT_DELAY_FRAMES}
frames (~{INPUT_DELAY_FRAMES * 1000 // FRAME_HZ}ms) after acceptance,
for every player. Network ping below that grants no edge. Plan ahead:
your view serves the ball's predicted arc at +30 and +60 frames.

## Your paddle — a scalpel in a stadium
A {PAD_W * 2:,.0f} x {PAD_H * 2:,.0f} rectangular face — tiny against
the court and small against the ball. You position, you predict arcs,
you pass to where a teammate will be. Programs command up to
{MAX_SEGMENTS} segments totalling <= {MAX_PROGRAM_MS} ms:
{{"action":"program","segments":[{{"ms":300,"vx":900000,"vy":-400000,
"vz":600000,"yaw":15,"pitch":-10,"force":85}}]}}
- vx/vy/vz: velocity in units/sec, each clamped to ±{V_MAX:,.0f}.
- yaw/pitch: where the FACE points. The ball rebounds along the face
  normal: your angle IS your shot. The face turns at
  {ANG_RATE * FRAME_HZ:g} deg/sec — aim early.
- force 0-100: soft ({BASE_REST:.2f}x) cushions and drop-passes; hard
  (~{BASE_REST + FORCE_GAIN:.1f}x) launches. Your swing transfers into
  the shot ({VEL_XFER:g}x). Passing IS striking with intent.

## Spacing is law
Teammates inside {SEP_DIST:,.0f} units are pushed apart by the
referee. Spread, hold lanes, receive.

## Dead balls
A ball below walking pace for 3 seconds re-serves from center.

## Talk — the game expects it
{{"action":"say","channel":"team","text":"…"}} — teammates only,
64 chars per 2-second window. Team chat arrives in every teammate's
context within a beat. A mute team is leaving coordination on the
table: with zones, a touch cap, and 200ms input delay, the callout IS
the playmaker. Spectators hear team talk on an ~8s broadcast delay;
opponents are never served it live. Space-time callouts win: name a
SPOT and a TIME ("P 0.72 0.40 t40" = ball arrives near x=72%, y=40%
in ~40 frames) so a teammate can BE there. Suggested protocol (data,
not law — invent better):
  "M"        I am taking this ball
  "P <x>"    pass is coming toward x (court fraction, e.g. P 0.78)
  "CLR"      clearing hard downfield now
  "W"        threat on our window, collapse

## Records
Every accepted program logs with its application frame; the match
re-simulates from spawn + program log to a digest anyone can verify.
"""

    def players_min(self) -> int: return 6
    def players_max(self) -> int: return 10

    def timing(self) -> dict:
        return {"frame_hz": FRAME_HZ, "cadence": "realtime",
                "match_seconds": self.match_seconds}

    def actions_schema(self) -> dict:
        seg = {"type": "object", "required": ["ms"],
               "properties": {
                   "ms": {"type": "integer", "minimum": 16},
                   "vx": {"type": "number", "minimum": -V_MAX, "maximum": V_MAX},
                   "vy": {"type": "number", "minimum": -V_MAX, "maximum": V_MAX},
                   "vz": {"type": "number", "minimum": -V_MAX, "maximum": V_MAX},
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
                 "budget_chars_per_window": 64}]

    def comms_window_frames(self) -> int:
        return FRAME_HZ * 2

    def observation_hint(self) -> int: return 420

    def assign_team(self, seat: int, params: dict):
        return "west" if seat % 2 == 0 else "east"

    def validate_params(self, params: dict):
        try:
            exp = int(params.get("expected_players", self.players_min()))
        except (TypeError, ValueError):
            return "expected_players must be an integer"
        if not (self.players_min() <= exp <= self.players_max()):
            return (f"expected_players must be {self.players_min()}-"
                    f"{self.players_max()}")
        if exp % 2:
            return ("teams are even here: expected_players must be "
                    "EVEN — nobody plays 4v3")
        return None

    def start_ok(self, n_players: int) -> bool:
        return n_players % 2 == 0          # even teams only

    def start_ok(self, n_players: int) -> bool:
        return n_players % 2 == 0      # uneven teams are impossible,
                                       # not merely discouraged

    # --------------------------------------------------------- lifecycle
    def on_start(self, players: list[Player], lobby: Lobby) -> None:
        self.match_seconds = float(lobby.params.get("match_seconds", 120))
        zones = bool(lobby.params.get("zones", True))
        roster = [{"name": p.name, "team": p.team} for p in players]
        self.world = build_world(roster, zones=zones)
        self.frame_override = 0
        lobby.emit("setup", True, None,
                   {"roster": roster, "match_seconds": self.match_seconds,
                    "arena": [AR_X, AR_Y, AR_Z],
                    "goal_y": GOAL_Y, "goal_z": GOAL_Z,
                    "pad": [PAD_W, PAD_H], "n_balls": N_BALLS,
                    "touch_cap": TOUCH_CAP, "zones": zones,
                    "input_delay_frames": INPUT_DELAY_FRAMES})

    async def run(self, lobby: Lobby) -> None:
        total = int(self.match_seconds * FRAME_HZ)
        wall = time.time()
        while self.world["frame"] < total:
            f = self.world["frame"]
            ready = [e for e in self.pending if e[0] <= f]
            if ready:
                self.pending = [e for e in self.pending if e[0] > f]
                for _, name, segs in sorted(ready, key=lambda e: e[1]):
                    apply_program(self.world, name, segs)
                    ev = {"frame": f, "player": name, "segments": segs}
                    self.program_log.append(ev)
                    lobby.emit("program", False, name, ev)
            for g in physics_step(self.world):
                lobby.emit("goal", True, None,
                           {**g, "frame": self.world["frame"],
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
                                  f"±{V_MAX:,.0f}, yaw ±180, pitch ±89, "
                                  "force 0-100}"}
            if (ms < 16 or any(abs(v) > V_MAX for v in vals.values())
                    or not (-180 <= yaw <= 180) or not (-89 <= pitch <= 89)
                    or not (0 <= force <= 100)):
                return {"accepted": False, "retry": True,
                        "reason": f"segment out of range: ms>=16, vx/vy/vz "
                                  f"±{V_MAX:,.0f} u/s, yaw ±180, pitch ±89, "
                                  "force 0-100"}
            total_ms += ms
            clean.append({"ms": ms, **vals, "yaw": yaw, "pitch": pitch,
                          "force": force})
        if total_ms > MAX_PROGRAM_MS:
            return {"accepted": False, "retry": True,
                    "reason": f"program too long: {total_ms}ms > "
                              f"{MAX_PROGRAM_MS}ms"}
        apply_at = self.world["frame"] + INPUT_DELAY_FRAMES
        self.pending = [e for e in self.pending if e[1] != player.name]
        self.pending.append((apply_at, player.name, clean))
        return {"accepted": True,
                "applies_at_frame": apply_at,
                "input_delay_frames": INPUT_DELAY_FRAMES,
                "note": "everyone's inputs land exactly "
                        f"{INPUT_DELAY_FRAMES} frames after acceptance — "
                        "ping buys no edge here",
                "total_ms": total_ms}

    # --------------------------------------------------------------- view
    def view(self, player, lobby: Lobby) -> dict:
        if self.world is None:
            # pre-kickoff: serve a faithful EXAMPLE so authoring minds
            # (forge) see the real shape even from a staged lobby
            return {"example": True,
                    "note": "match not started; a real view has exactly "
                            "this shape once it is",
                    "arena": [AR_X, AR_Y, AR_Z], "touch_cap": TOUCH_CAP,
                    "frame": 0, "score": {"west": 0, "east": 0},
                    "balls": [{"x": 2000000.0, "y": 1200000.0,
                               "z": 900000.0, "vx": -9000.0,
                               "vy": 3200.0, "vz": 5200.0,
                               "arc": {"f30": [1730000.0, 1296000.0,
                                               949000.0],
                                       "f60": [1460000.0, 1392000.0,
                                               882000.0]},
                               "last_toucher": "", "touch_team": "",
                               "touch_count": 0, "you_may_touch": True}],
                    "you": {"x": 600000.0, "y": 1200000.0, "z": 800000.0,
                            "vx": 0.0, "vy": 0.0, "vz": 0.0, "yaw": 0.0,
                            "pitch": 0.0, "force": 30.0, "team": "west",
                            "zone_x": [2200000.0, 4000000.0]},
                    "goal_you_attack": {"x": 4000000.0,
                                        "y_range": list(GOAL_Y),
                                        "z_range": list(GOAL_Z)},
                    "near": []}
        w = self.world
        base = {"frame": w["frame"], "score": w["score"],
                "frames_left": max(0, int(self.match_seconds * FRAME_HZ)
                                   - w["frame"]),
                "arena": [AR_X, AR_Y, AR_Z], "touch_cap": TOUCH_CAP,
                "balls": [{k: b[k] for k in ("x", "y", "z",
                                             "vx", "vy", "vz")}
                          for b in w["balls"]]}
        if player is None:
            base["teams"] = {t: sum(1 for p in w["paddles"].values()
                                    if p["team"] == t)
                             for t in ("west", "east")}
            return base
        me = w["paddles"].get(player.name)
        if me is None:
            return base
        for i, b in enumerate(w["balls"]):
            base["balls"][i]["arc"] = _ball_arc(b)
            base["balls"][i]["last_toucher"] = b["lt"]
            base["balls"][i]["touch_team"] = b["tteam"]
            base["balls"][i]["touch_count"] = b["tcount"]
            base["balls"][i]["you_may_touch"] = _may_touch(
                b, player.name, me["team"])
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
                "you": {**{k: me[k] for k in
                           ("x", "y", "z", "vx", "vy", "vz",
                            "yaw", "pitch", "force", "team")},
                        "zone_x": [me["bx0"], me["bx1"]]},
                "goal_you_attack": {"x": attack_x, "y_range": GOAL_Y,
                                    "z_range": GOAL_Z},
                "near": near[:4]}

    def committed(self, player: Player) -> bool:
        return False

    def policy_skeleton(self) -> str:
        return _SKELETON

    @staticmethod
    def replay_frames(events: list[dict], step: int = 6) -> dict | None:
        setup = next((e for e in events if e["kind"] == "setup"), None)
        if setup is None:
            return None
        progs = sorted((e["data"] for e in events
                        if e["kind"] == "program" and isinstance(e["data"], dict)),
                       key=lambda d: (d["frame"], d["player"]))
        world = build_world(setup["data"]["roster"],
                            zones=setup["data"].get("zones", True))
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
                               "b": [[b["x"], b["y"], b["z"]]
                                     for b in world["balls"]],
                               "m": [[b["lt"], b["tteam"], b["tcount"]]
                                     for b in world["balls"]],
                               "v": {n: [p["x"], p["y"], p["z"],
                                         p["yaw"], p["pitch"]]
                                     for n, p in world["paddles"].items()}})
        return {"kind": "prang2", "fps": FRAME_HZ / step,
                "arena": [AR_X, AR_Y, AR_Z],
                "goal_y": list(GOAL_Y), "goal_z": list(GOAL_Z),
                "pad": [PAD_W, PAD_H], "ball_r": BALL_R,
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
                "pad": [PAD_W, PAD_H], "ball_r": BALL_R,
                "balls": [[b["x"], b["y"], b["z"]] for b in w["balls"]],
                "balls_meta": [{"lt": b["lt"], "tteam": b["tteam"],
                                "tcount": b["tcount"]}
                               for b in w["balls"]],
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
    world = build_world(setup["data"]["roster"],
                        zones=setup["data"].get("zones", True))
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


# Served to authoring minds by forge: a WORKING policy whose plumbing
# is correct so authors spend their tokens on strategy, not stdin
# loops. Comms are not optional in the framework — the say/listen
# helpers are wired and the decide path uses them.
_SKELETON = '''#!/usr/bin/env python3
"""STARTER FRAMEWORK — Prang II policy (proc protocol).

The plumbing below is correct and fast. YOUR JOB is the strategy in
the sections marked >>> TUNE. Restructure freely, but keep:
  - one JSON line out per "decide" line in, always flushed
  - "null" for anything else
  - decides under ~50ms (pure arithmetic)
COMMUNICATION IS THE GAME: this framework speaks and listens. Name a
SPOT and a TIME when you pass; move teammates to called spots. Invent
better callouts than these — your team, your language.
"""
import json, math, sys

state = {"tick": 0, "last_say": -999}

def clamp(v, lo, hi): return max(lo, min(hi, v))

def predict(ball, t):
    """Where the served arc says the ball will be in t frames."""
    arc = ball.get("arc") or {}
    p0 = [ball["x"], ball["y"], ball["z"]]
    a, b = arc.get("f30") or p0, arc.get("f60") or p0
    if t <= 30:
        f = t / 30.0
        return [p0[i] + (a[i] - p0[i]) * f for i in range(3)]
    f = clamp((t - 30) / 30.0, 0, 1)
    return [a[i] + (b[i] - a[i]) * f for i in range(3)]

def aim(you, tx, ty, tz):
    """Face the target: the ball rebounds along your face normal."""
    dx, dy, dz = tx - you["x"], ty - you["y"], tz - you["z"]
    yaw = math.degrees(math.atan2(dy, dx))
    pitch = math.degrees(math.atan2(dz, math.hypot(dx, dy) or 1.0))
    return round(yaw, 1), round(clamp(pitch, -89, 89), 1)

def calls(ctx, me, horizon=240):
    """Teammates\' recent callouts, newest first (already team-only)."""
    frame = (ctx.get("view") or {}).get("frame", 0)
    out = []
    for m in reversed(ctx.get("comms") or []):
        if m.get("from") != me and frame - m.get("frame", 0) <= horizon:
            out.append(m.get("text", ""))
    return out

def say(text, frame, min_gap=130):
    """One callout per ~2s budget window. Returns an action or None."""
    if frame - state["last_say"] < min_gap:
        return None
    state["last_say"] = frame
    return {"action": "say", "channel": "team", "text": text[:60]}

def program(you, move_to, face_at, force, ms=250, vgain=5.0, vmax=2500000.0):
    yaw, pitch = aim(you, *face_at)
    return {"action": "program", "segments": [{
        "ms": ms,
        "vx": round(clamp((move_to[0] - you["x"]) * vgain, -vmax, vmax), 1),
        "vy": round(clamp((move_to[1] - you["y"]) * vgain, -vmax, vmax), 1),
        "vz": round(clamp((move_to[2] - you["z"]) * vgain, -vmax, vmax), 1),
        "yaw": yaw, "pitch": pitch, "force": force}]}

def decide(ctx):
    v = ctx.get("view") or {}
    you, balls = v.get("you"), v.get("balls") or []
    if not you or not balls:
        return None
    me = (ctx.get("you") or {}).get("name", "")
    seat = (ctx.get("you") or {}).get("seat", 0)
    frame = v.get("frame", 0)
    A = v.get("arena") or [4000000.0, 2400000.0, 1600000.0]
    X, Y, Z = float(A[0]), float(A[1]), float(A[2])
    g = v.get("goal_you_attack") or {}
    gx = float(g.get("x", X))
    gy = sum(g.get("y_range", [Y/2]*2)) / 2
    gz = sum(g.get("z_range", [Z/2]*2)) / 2
    z0, z1 = you.get("zone_x") or [0, X]
    b = balls[0]
    bp = predict(b, 18)            # 12f input delay + travel margin
    dist = math.dist((you["x"], you["y"], you["z"]), tuple(bp))
    heard = calls(ctx, me)

    # >>> TUNE: obey teammates\' space-time callouts ("P fx fy tNN")
    for c in heard:
        if c.startswith("P ") and b.get("you_may_touch", True):
            try:
                fx, fy = float(c.split()[1]), float(c.split()[2])
                spot = (clamp(fx * X, z0, z1), fy * Y, Z * 0.45)
                return program(you, spot, (bp[0], bp[1], bp[2]), 45)
            except (ValueError, IndexError):
                pass

    # >>> TUNE: engage when legal and reachable
    if (b.get("you_may_touch", True) and dist < 0.25 * X and
            (z0 - 0.05 * X) <= bp[0] <= (z1 + 0.05 * X)):
        # announce the pass BEFORE striking (input delay = time to move)
        pocket_fx = 0.78 if gx > X / 2 else 0.22
        callout = say(f"P {pocket_fx:.2f} {gy/Y:.2f} t40", frame)
        if callout:
            return callout
        # >>> TUNE: force + target choice = your whole offense
        return program(you, tuple(bp), (gx, gy, gz), 85)

    # >>> TUNE: positioning when you cannot touch (be the receiver!)
    station = (clamp(bp[0], z0 + 0.05 * X, z1 - 0.05 * X),
               clamp(bp[1], 0.1 * Y, 0.9 * Y),
               clamp(bp[2], 0.15 * Z, 0.85 * Z))
    return program(you, station, tuple(bp), 40)

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            out = decide(obj) if obj.get("mode") == "decide" else None
            print(json.dumps(out) if out else "null", flush=True)
        except Exception:
            print("null", flush=True)

if __name__ == "__main__":
    main()
'''
