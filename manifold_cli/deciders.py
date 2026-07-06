"""Deciders: the minds a pilot can carry. The pilot itself is generic;
deciders are players, and players may know games. Three families:

  mock:<style>      scripted stand-in minds for tests and demos
  cmd:<program>     the universal socket: stdin context -> stdout action
  anthropic:<model> a generic LLM mind (your key, your shell, your bill)
"""

from __future__ import annotations

import json
import math
import re
import subprocess


def make_decider(spec: str):
    kind, _, arg = spec.partition(":")
    if kind == "mock":
        return {"converge": MockConverge, "hold": MockHold,
                "fogline-brash": MockFoglineBrash,
                "fogline-measured": MockFoglineMeasured,
                "prang-chase": MockPrangChase,
                "prang-striker": MockPrangStriker}[arg]()
    if kind == "cmd":
        return CmdDecider(arg)
    if kind == "anthropic":
        return AnthropicDecider(arg or "claude-sonnet-4-6")
    raise SystemExit(f"unknown decider '{spec}'")


# ------------------------------------------------------------- mocks
class MockHold:
    def __init__(self):
        self._said = False

    def decide(self, ctx):
        v = ctx.get("view") or {}
        chans = (ctx.get("manifest", {}).get("comms") or {}).get("channels")
        if v.get("tick") == 2 and chans and not self._said:
            self._said = True
            return {"action": "say", "channel": chans[0]["id"],
                    "text": "quiet water",
                    "reasoning": "testing the harbor channel"}
        return {"action": "none"}

    def reflect(self, ctx):
        return None


class MockConverge:
    """Round 1: a name-derived word. Later: the alphabetically first word
    from the previous reveal. Two of these converge in round 2."""

    WORDS = ["anchor", "beacon", "current", "driftwood", "estuary",
             "fathom", "gull", "harbor"]

    def decide(self, ctx):
        v = ctx["view"]
        hist = v.get("history") or []
        if not hist:
            you = (ctx.get("you") or {}).get("name", "x")
            return {"action": "word",
                    "word": self.WORDS[sum(map(ord, you)) % len(self.WORDS)],
                    "reasoning": "seeding from my own name"}
        words = sorted(w.lower() for w in hist[-1]["words"].values()
                       if w != "...")
        return {"action": "word", "word": (words[0] if words else "anchor"),
                "reasoning": "converging on the alphabetical focal point"}

    def reflect(self, ctx):
        r = ctx.get("result") or {}
        return {"playbook_md": "# Playbook — convergence\n\n- Alphabetical "
                               "minimum of the last reveal is a strong focal "
                               f"point (converged: {r.get('converged')}).\n",
                "strategy_md": "# Strategy — transferable\n\n- Coordination "
                               "without communication is solved by shared "
                               "focal points; pick the rule everyone can "
                               "derive, not the clever one.\n"}


def _clue_ints(view, tick):
    for c in view.get("clues_revealed", []):
        if c.get("tick") == tick:
            return [int(x) for x in re.findall(r"\d+", c["clue"])]
    return []


def _cap(view):
    caps = view.get("tick_exposure_caps") or [0.25]
    t = int(view.get("tick", 1))
    return caps[min(t, len(caps)) - 1] * float(view.get("liquid_bankroll", 0))


class MockFoglineBrash:
    def decide(self, ctx):
        v = ctx["view"]; t = v.get("tick")
        d = v["announcement"]["island"]["domain"]
        lo_d, hi_d = float(d[0]), float(d[1]); W = hi_d - lo_d
        if t == 1:
            return {"action": "stake", "lo": lo_d + 0.02 * W,
                    "hi": lo_d + 0.26 * W, "confidence": 0.9,
                    "exposure": round(_cap(v) * 0.98, 2),
                    "reasoning": "fortune favors the loud"}
        if t == 4:
            return {"action": "stake", "lo": lo_d + 0.05 * W,
                    "hi": lo_d + 0.23 * W, "confidence": 0.9,
                    "exposure": round(_cap(v) * 0.95, 2),
                    "reasoning": "double or nothing"}
        return {"action": "none"}

    def reflect(self, ctx):
        return {"playbook_md": "# Playbook\n\n- (mock) louder next time.\n",
                "strategy_md": "# Strategy\n\n- (mock) conviction is free, "
                               "right?\n"}


class MockFoglineMeasured:
    def decide(self, ctx):
        v = ctx["view"]; t = v.get("tick")
        d = v["announcement"]["island"]["domain"]
        lo_d, hi_d = float(d[0]), float(d[1]); W = hi_d - lo_d

        def band(est, frac):
            half = min(frac * est, 0.115 * W)
            return max(lo_d, round(est - half)), min(hi_d, round(est + half))

        if t == 2 and not v.get("your_probe_results"):
            probes = v["announcement"]["island"].get("probes") or []
            if probes:
                return {"action": "probe", "probe_id": probes[0]["id"],
                        "reasoning": "buy edge while it is private"}
        if t == 4:
            ints = _clue_ints(v, 4)
            if ints:
                lo, hi = band(ints[0] * 1600, 0.5)
                return {"action": "stake", "lo": lo, "hi": hi,
                        "confidence": 0.7,
                        "exposure": round(0.6 * _cap(v), 2),
                        "reasoning": "schoolhouses anchor the Fermi chain"}
        if t == 5:
            ints = [x for x in _clue_ints(v, 5) if x >= 50]
            if ints:
                lo, hi = band(ints[0] / 0.25, 0.22)
                return {"action": "stake", "lo": lo, "hi": hi,
                        "confidence": 0.8,
                        "exposure": round(0.7 * _cap(v), 2),
                        "reasoning": "district share pins it down"}
        return {"action": "none"}

    def reflect(self, ctx):
        return {"playbook_md": "# Playbook\n\n- (mock) probes early, stakes "
                               "on synthesis.\n",
                "strategy_md": "# Strategy\n\n- (mock) size follows "
                               "evidence.\n"}


class MockPrangStriker:
    """Role soccer, tuned in a headless physics lab: presser + sweeper
    beat two ball-chasers 7W-7D-2L, 18-5 on goals, over 16 offset-seeded
    matches. Position play is the edge — the chasers both abandon their
    goal; the sweeper holds it. First seat of a team presses, second
    sweeps (role = seat // 2)."""

    def _steer(self, you, tx, ty, thrust_base, thrust_gain):
        dx, dy = tx - you["x"], ty - you["y"]
        want = math.degrees(math.atan2(dy, dx))
        diff = (want - you["ang"] + 540) % 360 - 180
        align = math.cos(math.radians(diff))
        thrust = max(0.0, min(100.0, thrust_base + thrust_gain * align))
        return diff, thrust

    def decide(self, ctx):
        v = ctx["view"]
        you, ball = v.get("you"), v.get("ball")
        if not you or not ball:
            return {"action": "none"}
        bx, by = ball["x"], ball["y"]
        dist = math.hypot(bx - you["x"], by - you["y"])
        g = v.get("goal_you_attack") or {}
        gx = float(g.get("x", 0.0))
        yr = g.get("y_range") or [450, 750]
        gy = (float(yr[0]) + float(yr[1])) / 2
        role = ((ctx.get("you") or {}).get("seat", 0)) // 2   # 0 press, 1 sweep

        if role == 1 and dist >= 140:
            # sweeper: hold a station 30% out from our own goal toward
            # the ball; the field is 2000 wide, own goal mirrors attack
            own_gx = 2000.0 - gx
            tx = own_gx + 0.30 * (bx - own_gx)
            ty = 600.0 + 0.30 * (by - 600.0)
            d = math.hypot(tx - you["x"], ty - you["y"])
            diff, thrust = self._steer(you, tx, ty, 30, 70)
            thrust = 0.0 if d < 25 else thrust * min(1.0, d / 200)
            reason = "holding the goal-side station"
        else:
            # presser: lead the served arc when far, take the ball when near
            arc = ball.get("arc") or {}
            tx, ty = (arc.get("f30") or [bx, by]) if dist > 140 else (bx, by)
            diff, thrust = self._steer(you, tx, ty, 40, 60)
            reason = "pressing the predicted ball"
        shot = (math.degrees(math.atan2(gy - you["y"], gx - you["x"]))
                - you["ang"] + 540) % 360 - 180
        return {"action": "program",
                "segments": [{"ms": 250, "thrust": round(thrust, 1),
                              "turn": max(-180.0, min(180.0, round(diff * 4, 1))),
                              "kick": dist < 90 and abs(shot) < 70}],
                "reasoning": reason}

    def reflect(self, ctx):
        return None


class MockPrangChase:
    def decide(self, ctx):
        v = ctx["view"]
        you, ball = v.get("you"), v.get("ball")
        if not you or not ball:
            return {"action": "none"}
        dx, dy = ball["x"] - you["x"], ball["y"] - you["y"]
        want = math.degrees(math.atan2(dy, dx))
        diff = (want - you["ang"] + 540) % 360 - 180
        turn = max(-180, min(180, diff / 0.3))
        return {"action": "program",
                "segments": [{"ms": 300, "thrust": 95, "turn": round(turn, 1),
                              "kick": True}],
                "reasoning": "chase and poke"}

    def reflect(self, ctx):
        return None


# ---------------------------------------------------------------- cmd
class CmdDecider:
    """stdin: {"mode":"decide"|"reflect", ...context} -> stdout: JSON."""

    def __init__(self, program: str):
        self.program = program

    def _call(self, payload: dict) -> dict | None:
        out = subprocess.run(self.program, shell=True, input=json.dumps(payload),
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(f"decider exited {out.returncode}: "
                               f"{out.stderr[:200]}")
        s = out.stdout.strip()
        return json.loads(s) if s else None

    def decide(self, ctx):
        return self._call({"mode": "decide", **ctx})

    def reflect(self, ctx):
        return self._call({"mode": "reflect", **ctx})


# ----------------------------------------------------------- anthropic
def _extract_json(text: str) -> dict:
    t = re.sub(r"```(?:json)?", "", text)
    start = t.find("{")
    if start == -1:
        raise ValueError("no JSON in reply")
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("unbalanced JSON")


class AnthropicDecider:
    """Generic: composes exactly the pilot-contract context. Knows no game."""

    def __init__(self, model: str):
        import anthropic  # only imported if you actually use this mind
        self.client = anthropic.Anthropic()
        self.model = model

    def _system(self, ctx) -> str:
        return (ctx["preamble"]
                + "\n\nGAME RULEBOOK (served data, hash-verified):\n<<<\n"
                + ctx["rulebook"] + "\n>>>\n\nYOUR TRANSFERABLE STRATEGY:\n"
                + ctx["strategy_md"] + "\n\nYOUR PLAYBOOK FOR THIS GAME:\n"
                + ctx["playbook_md"]
                + ("\n\nTEAM PLAYBOOK:\n" + ctx["team_playbook_md"]
                   if ctx.get("team_playbook_md") else ""))

    def _ask(self, system, user, max_tokens=1200):
        msg = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if b.type == "text")

    def decide(self, ctx):
        user = ("GAME STATE:\n" + json.dumps(
                    {k: ctx[k] for k in ("you", "phase", "frame",
                                          "deadline_utc", "view", "comms")},
                    indent=1)
                + "\n\nACTION SCHEMA:\n" + json.dumps(ctx["action_schema"])
                + (f"\n\nREFEREE FEEDBACK: {ctx['referee_feedback']}"
                   if ctx.get("referee_feedback") else "")
                + "\n\nDecide. You may include a short private \"reasoning\" "
                  "field. Reply with ONLY one JSON action object.")
        return _extract_json(self._ask(self._system(ctx), user))

    def reflect(self, ctx):
        base = ("MATCH RESULT:\n" + json.dumps(ctx.get("result"))
                + "\n\nYOUR ACTIONS:\n" + json.dumps(ctx.get("your_actions")))
        pb = self._ask(
            "You maintain a compact per-game playbook (<=350 words, "
            "markdown). Distill how to win THIS game; do not accumulate. "
            "Reply ONLY the new markdown.",
            base + "\n\nCURRENT PLAYBOOK:\n" + ctx["playbook_md"]
            + "\n\nRewrite the full playbook.")
        st = self._ask(
            "You maintain a transferable strategy document (<=350 words, "
            "markdown): reasoning principles that outlive any one game. "
            "PORTABILITY TEST: every line must be useful verbatim at a live "
            "trading desk. EXPLOIT GUARD: lessons that only work because of "
            "one game's payout formula belong in that game's playbook, "
            "never here. Back each principle with an evidence line. "
            "Reply ONLY the new markdown.",
            base + "\n\nCURRENT STRATEGY:\n" + ctx["strategy_md"]
            + "\n\nRewrite the full strategy document.")
        return {"playbook_md": pb.strip()[:6000],
                "strategy_md": st.strip()[:6000]}
