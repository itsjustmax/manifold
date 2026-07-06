"""Convergence: the Manifold hello-world.

Everyone submits one word per round. If all words match, the table
wins — fewer rounds, higher score. Full history is public. The entire
game is: model your co-players' minds.
"""

from __future__ import annotations

import asyncio
import time

from ..kit import FRAME_HZ, Game, Lobby, Player, iso

MAX_ROUNDS = 8


class Convergence(Game):
    ID = "convergence"
    NAME = "Convergence"
    VERSION = "1.0"
    SKILLS = ["theory-of-mind", "coordination"]

    def __init__(self):
        self.round = 0
        self.history: list[dict] = []          # [{round, words:{name:word}}]
        self.commits: dict[str, str] = {}      # name -> word (this round)
        self.deadline: float | None = None
        self.round_seconds = 30
        self.converged_round: int | None = None
        self.names: list[str] = []

    # ------------------------------------------------------- description
    def rulebook(self) -> str:
        return (
            "# Convergence — Rulebook v1\n\n"
            "You and the other players win together by all submitting the "
            "SAME word in the same round.\n\n"
            f"- Up to {MAX_ROUNDS} rounds. Each round, submit exactly one "
            "word (1-24 letters/digits, lowercase compared).\n"
            "- After each round every player's word is revealed to everyone.\n"
            "- If all words match, the table scores (9 minus the round "
            "number) points each. If round 8 passes without convergence, "
            "everyone scores 0.\n"
            "- A missed deadline submits '...' for you that round.\n\n"
            "Strategy is theory of mind: converge toward the word the "
            "others will converge toward. History is your only signal.\n"
        )

    def players_min(self) -> int: return 2
    def players_max(self) -> int: return 8

    def timing(self) -> dict:
        return {"frame_hz": FRAME_HZ, "cadence": "turns",
                "decision_window_s": self.round_seconds}

    def actions_schema(self) -> dict:
        return {"oneOf": [
            {"type": "object", "required": ["action", "word"],
             "properties": {"action": {"const": "word"},
                            "word": {"type": "string", "minLength": 1,
                                     "maxLength": 24,
                                     "pattern": "^[A-Za-z0-9]+$"}}},
        ]}

    def observation_hint(self) -> int: return 150

    # --------------------------------------------------------- lifecycle
    def on_start(self, players: list[Player], lobby: Lobby) -> None:
        self.round_seconds = float(lobby.params.get("round_seconds", 30))
        self.names = [p.name for p in players]

    async def run(self, lobby: Lobby) -> None:
        for r in range(1, MAX_ROUNDS + 1):
            self.round = r
            self.commits = {}
            self.deadline = time.time() + self.round_seconds
            lobby.emit("round", True, None, {"round": r})
            while time.time() < self.deadline and len(self.commits) < len(self.names):
                await asyncio.sleep(0.05)
            words = {n: self.commits.get(n, "...") for n in self.names}
            self.history.append({"round": r, "words": words})
            lobby.emit("reveal", True, None, {"round": r, "words": words})
            vals = {w.lower() for w in words.values()}
            if len(vals) == 1 and "..." not in vals:
                self.converged_round = r
                return

    def result(self) -> dict:
        score = (MAX_ROUNDS + 1 - self.converged_round) if self.converged_round else 0
        return {"converged": self.converged_round is not None,
                "round": self.converged_round, "score_each": score,
                "history": self.history}

    # ------------------------------------------------------- per-request
    def on_action(self, player: Player, action: dict, lobby: Lobby,
                  reasoning: str = "") -> dict:
        if action.get("action") != "word":
            return {"accepted": False, "retry": True,
                    "reason": "only action here is {\"action\":\"word\",\"word\":…}"}
        w = str(action.get("word", ""))
        if not (1 <= len(w) <= 24) or not w.isalnum():
            return {"accepted": False, "retry": True,
                    "reason": "word must be 1-24 letters/digits"}
        if player.name in self.commits:
            if self.commits[player.name].lower() == w.lower():
                return {"accepted": True, "note": "already committed"}
            return {"accepted": False, "retry": False,
                    "reason": "already committed this round; words are final"}
        self.commits[player.name] = w
        lobby.emit("word_committed", False, player.name,
                   {"round": self.round, "word": w, "reasoning": reasoning})
        return {"accepted": True, "round": self.round}

    def view(self, player, lobby: Lobby) -> dict:
        return {"round": self.round, "max_rounds": MAX_ROUNDS,
                "players": self.names, "history": self.history,
                "committed_count": len(self.commits)}

    def committed(self, player: Player) -> bool:
        return player.name in self.commits

    def deadline_utc(self):
        return iso(self.deadline) if self.deadline else None
