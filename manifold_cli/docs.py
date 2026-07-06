"""Document tiers. Strategy attaches to the identity and rides across
games (and into real work); playbooks are game-scoped; team playbooks
are shared craft. All live with the pilot, never on any server."""

from __future__ import annotations

import time
from pathlib import Path

STRATEGY_SEED = """# Strategy — transferable (fresh)

Principles proven across games, written to survive outside them.
Portability test: every line must be useful verbatim at a trading desk.

## Epistemic
- (none proven yet)

## Risk & sizing
- (none proven yet)

## Evidence log
- (match ids and outcomes backing each principle)
"""

PLAYBOOK_SEED = """# Playbook (fresh)

- No experience with this game yet. Read the served rulebook closely and
  reason from first principles.
"""


class Docs:
    def __init__(self, home: Path, identity: str, game: str):
        self.identity_dir = home / "identities" / identity
        self.game_dir = self.identity_dir / "games" / game
        self.history = self.game_dir / "history"
        self.game_dir.mkdir(parents=True, exist_ok=True)
        self._ensure(self.identity_dir / "strategy.md", STRATEGY_SEED)
        self._ensure(self.game_dir / "playbook.md", PLAYBOOK_SEED)

    @staticmethod
    def _ensure(p: Path, seed: str) -> None:
        if not p.exists():
            p.write_text(seed)

    def strategy(self) -> str:
        return (self.identity_dir / "strategy.md").read_text()

    def playbook(self) -> str:
        return (self.game_dir / "playbook.md").read_text()

    def team_playbook(self, team: str | None) -> str:
        if not team:
            return ""
        p = self.identity_dir / "teams" / f"{team}.md"
        return p.read_text() if p.exists() else ""

    def _snap(self, p: Path) -> None:
        self.history.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (self.history / f"{p.stem}.{stamp}.md").write_text(p.read_text())

    def write_playbook(self, text: str) -> None:
        p = self.game_dir / "playbook.md"
        self._snap(p)
        p.write_text(text.strip()[:6000] + "\n")

    def write_strategy(self, text: str) -> None:
        p = self.identity_dir / "strategy.md"
        self._snap(p)
        p.write_text(text.strip()[:6000] + "\n")
