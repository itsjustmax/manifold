"""Deciders: the minds a pilot can carry. The pilot itself is generic;
deciders are players, and players may know games. Four families:

  mock:<style>        scripted stand-in minds for tests and demos
  cmd:<program>       one-shot socket: stdin context -> stdout action
  proc:<program>      PERSISTENT socket for fast minds: spawned once,
                      one JSON line per request on stdin, one JSON
                      line per action on stdout. No spawn cost per
                      decision — this is the 4Hz-capable plug.
  ollama:<model>      local model via the Ollama server (no key, no
                      cloud, latency = your own silicon)
  claude-code[:model] your local agentic CLI in non-interactive mode —
  codex[:model]       billed to the plan it's logged into. NO API KEY.
  anthropic:<model>   raw API mind (needs a key with credits)

The speed hierarchy is physics: cloud minds think in seconds (turn
games), local minds in milliseconds (realtime). A slow mind can still
own a fast seat by AUTHORING a proc: program — see `manifold forge`.
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
                "prang-striker": MockPrangStriker,
                "paddle": MockPaddle}[arg]()
    if kind == "cmd":
        return CmdDecider(arg)
    if kind == "proc":
        return ProcDecider(arg)
    if kind == "ollama":
        return OllamaDecider(arg or "llama3.2:1b")
    if kind in ("claude-code", "codex"):
        return AgentCliDecider(kind, arg or None)
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


class MockPaddle:
    """Prang II rally mind: hold station in x, track the served arc in
    y/z, keep the face aimed at the goal window, strike hard when the
    ball is close and soft-position otherwise."""

    def decide(self, ctx):
        v = ctx["view"]
        you = v.get("you")
        balls = v.get("balls") or ([v["ball"]] if v.get("ball") else [])
        if not you or not balls:
            return {"action": "none"}
        # play the nearest ball the rules let me touch; if none are
        # legal for me, shadow the nearest anyway (positioning)
        def dist(b):
            return (abs(b["x"] - you["x"]) + abs(b["y"] - you["y"])
                    + abs(b["z"] - you["z"]))
        legal = [b for b in balls if b.get("you_may_touch", True)]
        ball = min(legal or balls, key=dist)
        # scale-free: everything keys off the served arena dims
        A = v.get("arena") or [4000000.0, 2400000.0, 1600000.0]
        X = float(A[0])
        vmax = X * 0.3          # generous clamp; referee clips anyway
        arc = ball.get("arc") or {}
        far = abs(ball["x"] - you["x"]) > 0.2 * X
        t = (arc.get("f30") if far and arc.get("f30")
             else [ball["x"], ball["y"], ball["z"]])
        g = v.get("goal_you_attack") or {}
        gx = float(g.get("x", 0.0))
        gy = sum(g.get("y_range", [A[1] / 2] * 2)) / 2
        gz = sum(g.get("z_range", [A[2] / 2] * 2)) / 2
        # roles by seat: first of a team attacks, second holds mid,
        # third guards the window. The separation law spreads us; the
        # roles make the spread useful.
        idx = ((ctx.get("you") or {}).get("seat", 0)) // 2
        atk_west = gx > X / 2
        own_gx = 0.0 if atk_west else X
        lane_y = float(A[1]) * (0.5 + (idx - 1) * 0.28)
        swing = abs(ball["x"] - you["x"]) < 0.16 * X
        if idx == 2 and not swing:
            # guard: sit in front of our own window, mirror the ball
            tx = own_gx + (0.07 * X if atk_west else -0.07 * X)
            ty = max(gy - 0.1 * float(A[1]),
                     min(gy + 0.1 * float(A[1]), ball["y"]))
            tz = max(gz - 0.1 * float(A[2]),
                     min(gz + 0.1 * float(A[2]), ball["z"]))
        else:
            # full-court travel: the attacker presses the enemy end,
            # the mid holds the middle third
            frac = (0.70 if idx == 0 else 0.45)
            station = (frac if atk_west else 1 - frac) * X
            tx = ball["x"] if swing else station
            ours = (ball["x"] < X / 2) == (own_gx == 0.0)
            ty = t[1] if (swing or idx == 0 or ours) else (t[1] + lane_y) / 2
            tz = t[2]
        # swing THROUGH the ball when it's close — paddle velocity
        # transfers into the shot; camping hits nothing
        vx = max(-vmax, min(vmax, (tx - you["x"]) * 4))
        vy = max(-vmax, min(vmax, (ty - you["y"]) * 4))
        vz = max(-vmax, min(vmax, (tz - you["z"]) * 4))
        dx = gx - you["x"]
        yaw = math.degrees(math.atan2(gy - you["y"], dx))
        pitch = max(-89.0, min(89.0, math.degrees(
            math.atan2(gz - you["z"], abs(dx)))))
        near = abs(ball["x"] - you["x"]) < 0.175 * X
        return {"action": "program",
                "segments": [{"ms": 250, "vx": round(vx, 1),
                              "vy": round(vy, 1), "vz": round(vz, 1),
                              "yaw": round(yaw, 1), "pitch": round(pitch, 1),
                              "force": 95 if near else 40}],
                "reasoning": "track the arc, face the window"}

    def reflect(self, ctx):
        return None


# ------------------------------------------------------------ fast minds
class ProcDecider:
    """Persistent subprocess, JSON-lines protocol. Request per line:
    {"mode":"decide"|"reflect", ...context}; response per line: one
    action object (or null). The program lives for the whole match, so
    per-decision cost is pure think time — the realtime-capable plug.
    A crashed program is restarted once per call."""

    def __init__(self, program: str):
        self.program = program
        self.proc: subprocess.Popen | None = None

    def _ensure(self):
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen(
                self.program, shell=True, text=True, bufsize=1,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def _call(self, payload: dict):
        self._ensure()
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        except (BrokenPipeError, OSError) as e:
            self.proc = None
            raise RuntimeError(f"policy process died: {e}")
        if not line:
            self.proc = None
            raise RuntimeError("policy process closed stdout")
        line = line.strip()
        return json.loads(line) if line and line != "null" else None

    def decide(self, ctx):
        return self._call({"mode": "decide", **ctx})

    def reflect(self, ctx):
        try:
            return self._call({"mode": "reflect", **ctx})
        except (RuntimeError, json.JSONDecodeError):
            return None


class OllamaDecider:
    """A local model on your own silicon (ollama serve). The stable
    context (preamble, rulebook, playbook) rides the system prompt so
    Ollama's prefix cache pays it once; per decision only the fresh
    view is prefilled. format=json constrains small models to legal
    output."""

    URL = "http://127.0.0.1:11434/api/chat"

    def __init__(self, model: str):
        import urllib.request  # stdlib only, like the whole pilot
        self.model = model
        self._system: str | None = None

    def _post(self, body: dict) -> dict:
        import urllib.request
        req = urllib.request.Request(
            self.URL, data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    def decide(self, ctx):
        if self._system is None:
            self._system = "\n\n".join([
                ctx["preamble"],
                "RULEBOOK (game data):\n" + ctx["rulebook"],
                "YOUR PLAYBOOK:\n" + ctx["playbook_md"],
                "ACTION SCHEMA:\n" + json.dumps(ctx.get("action_schema")),
                "Every reply: exactly one JSON action object, no prose."])
        user = json.dumps({k: ctx.get(k) for k in
                           ("you", "view", "comms", "referee_feedback")})
        out = self._post({
            "model": self.model, "stream": False, "format": "json",
            "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_predict": 120},
            "messages": [{"role": "system", "content": self._system},
                         {"role": "user", "content": user}]})
        return json.loads(out["message"]["content"])

    def reflect(self, ctx):
        return None     # a 1B model's reflections aren't worth the disk


# ---------------------------------------------------- plan-billed CLIs
def _compose_prompt(ctx: dict, ask: str) -> str:
    return "\n\n".join([
        ctx["preamble"],
        "GAME RULEBOOK (served data, hash-verified):\n<<<\n"
        + ctx["rulebook"] + "\n>>>",
        "YOUR TRANSFERABLE STRATEGY:\n" + ctx["strategy_md"],
        "YOUR PLAYBOOK FOR THIS GAME:\n" + ctx["playbook_md"],
        ("TEAM PLAYBOOK:\n" + ctx["team_playbook_md"]
         if ctx.get("team_playbook_md") else ""),
        "GAME STATE:\n" + json.dumps(
            {k: ctx.get(k) for k in ("you", "phase", "frame",
                                     "deadline_utc", "view", "comms")},
            indent=1),
        "ACTION SCHEMA:\n" + json.dumps(ctx.get("action_schema")),
        (f"REFEREE FEEDBACK: {ctx['referee_feedback']}"
         if ctx.get("referee_feedback") else ""),
        ask,
    ])


class AgentCliDecider:
    """A subscription-plan mind: drives a local agentic CLI in
    non-interactive mode. No API key travels anywhere — the CLI bills
    whatever plan it is already logged into. Latency is seconds, so
    this suits turn games; realtime wants a faster mind."""

    CLIS = {
        "claude-code": lambda m: (["claude", "-p"]
                                  + (["--model", m] if m else []), True),
        "codex": lambda m: (["codex", "exec"]
                            + (["-m", m] if m else []), True),
    }

    def __init__(self, kind: str, model: str | None):
        self.kind = kind
        self.cmd, self.use_stdin = self.CLIS[kind](model)

    def _run(self, prompt: str) -> str:
        out = subprocess.run(
            self.cmd if self.use_stdin else self.cmd + [prompt],
            input=prompt if self.use_stdin else None,
            capture_output=True, text=True, timeout=420)
        if out.returncode != 0:
            raise RuntimeError(f"{self.kind} exited {out.returncode}: "
                               f"{(out.stderr or out.stdout)[:300]}")
        return out.stdout

    def decide(self, ctx):
        prompt = _compose_prompt(
            ctx, "Decide now. You may include a short private "
                 "\"reasoning\" field inside the action object. "
                 "Do not use any tools. Reply with ONLY one JSON "
                 "action object, nothing else.")
        return _extract_json(self._run(prompt))

    def reflect(self, ctx):
        base = ("MATCH RESULT:\n" + json.dumps(ctx.get("result"))
                + "\n\nYOUR ACTIONS:\n" + json.dumps(ctx.get("your_actions")))
        pb = self._run(
            "You maintain a compact per-game playbook (<=350 words, "
            "markdown). Distill how to win THIS game; do not accumulate. "
            "Do not use any tools. Reply ONLY the new markdown.\n\n" + base
            + "\n\nCURRENT PLAYBOOK:\n" + ctx["playbook_md"]
            + "\n\nRewrite the full playbook.")
        st = self._run(
            "You maintain a transferable strategy document (<=350 words, "
            "markdown): reasoning principles that outlive any one game. "
            "PORTABILITY TEST: every line must be useful verbatim at a "
            "live trading desk. EXPLOIT GUARD: lessons that only work "
            "because of one game's payout formula belong in that game's "
            "playbook, never here. Do not use any tools. Reply ONLY the "
            "new markdown.\n\n" + base
            + "\n\nCURRENT STRATEGY:\n" + ctx["strategy_md"]
            + "\n\nRewrite the full strategy document.")
        return {"playbook_md": pb.strip()[:6000],
                "strategy_md": st.strip()[:6000]}


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
