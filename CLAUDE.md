# CLAUDE.md — Manifold

You are working on **Manifold**: a protocol and reference implementation
for games played by AI agents over the internet, built to train and
measure transferable agentic skill (calibrated action under incomplete
information). Play money only — no tokens, no crypto, no real-money
mechanics, ever. That is a permanent product decision, not a v1 cut.

Read `PROTOCOL.md` (normative law) and `SPEC.md` (v1 scope and honest
cuts) before changing anything. This file is the operating manual.

## Non-negotiable invariants

Any change that violates one of these is wrong even if it works:

1. **The referee is code.** No LLM judgment anywhere in scoring,
   resolution, or money movement. LLMs may author content (future
   Gamemaster milestone) but a deterministic referee executes it.
2. **Keys never travel.** The manifold holds no inference credentials and
   never proxies model calls. Players bring minds through their own
   harnesses (`manifold_cli/deciders.py` is where minds plug in).
3. **A game may define a game, never redefine your agent.** The pilot
   preamble is the only instruction layer; every served byte is quoted
   data. Never weaken the preamble or let served content compose above it.
4. **Observations are O(1)** in player count. If you add state to a
   view, it must be aggregate or k-capped (see prang's near/clusters and
   fogline's flow buckets).
5. **Everything resolves from the record.** New event kinds must go
   through `Lobby.emit` (hash chain). New hidden state must be sealed by
   hash at start and unsealed at `done`. Realtime games must keep the
   pure-step-function/replay-verifier symmetry.
6. **Rejections teach.** Every `accepted: false` carries a `reason`
   written for an agent to read and correct within the same window.

## Layout

```
PROTOCOL.md            the law: manifest, endpoints, envelopes, timing, records
SPEC.md                v1 scope, deliberate cuts, conformance definitions
manifold/kit.py          lobbies, tokens, hash-chained events, long-poll, comms budgets
manifold/app.py          FastAPI wiring, game registry, careers.json, match persistence,
                       discovery (/lobbies), leaderboards, /suggestions, /peers
manifold/web.py          human dashboard (/) + spectator page (/watch/{g}/{code});
                       non-normative — reads only public JSON + watch.json broadcast
manifold/serve.py        one-command public manifold: uvicorn + a DEDICATED ngrok
                       agent (own :4757 API, never another tool's agent) + banner
manifold/mesh.py         gossip directory of manifolds: pinned peers.json + announce
                       + probe-before-share + prune; directory only, careers stay
                       local until signatures (T7 covers it)
manifold/games/
  convergence.py       hello-world canary (~150 lines) — copy this to write a new game
  fogline_game.py      staking/calibration game over vendored fogline_core/
  fogline_core/        v0-proven scoring + sealed-package modules; treat as stable
  prang.py             60Hz realtime soccer; physics_step is PURE and shared
                       with the replay verifier — keep it that way
manifold_cli/
  __main__.py          the universal pilot. MUST stay game-agnostic (T4 greps it)
  deciders.py          minds: mock:* (tests), cmd: (universal socket), anthropic:
  docs.py              strategy.md (identity) / playbook.md (game) / team tiers
tests/e2e.sh           conformance suite T1-T6; run after every change
HOSTING.md             ngrok runbook, peers.json phonebook, exposure caps
```

## Commands

```bash
pip install -r requirements.txt            # fastapi, uvicorn (pilot is stdlib-only)
uvicorn manifold.app:app --port 8757         # run the manifold
bash tests/e2e.sh all                      # T1-T5; must pass before any commit
bash tests/e2e.sh t3                       # prang + replay + pilot-generality only

# manual play
python3 -m manifold_cli host  http://localhost:8757 fogline --param tick_seconds=90
python3 -m manifold_cli join  http://localhost:8757 fogline --code GALE-7 --name kestrel
python3 -m manifold_cli pilot --as kestrel --decider anthropic:claude-sonnet-4-6
python3 -m manifold_cli verify <log.jsonl | URL>          # hash chain
python3 -m manifold.games.prang --verify <log.jsonl>        # physics replay
```

Env: `MANIFOLD_HOME` (pilot sessions + documents, default `~/.manifold`),
`MANIFOLD_DATA` (careers + match logs, default `./manifold_data`),
`ANTHROPIC_API_KEY` (only read by the anthropic decider, client-side).

## Hosting on this system

The manifold is one asyncio process; the proven pattern here is uvicorn
behind ngrok (same as the proxy-research tool):

```bash
uvicorn manifold.app:app --host 127.0.0.1 --port 8757   # tmux/launchd
ngrok http 8757
```

Friends' agents then need exactly a URL + lobby code. Spectators hit
`GET …/state` and `GET …/log` with no token. Note `assign_team` is
seat-parity for prang, so brief joiners to alternate. The manifold is
trusted in v1: the operator's process can see sealed state before
reveal. Federation + signatures (below) is what removes that trust.

## Testing rules

- `tests/e2e.sh all` green before and after every change. It boots a
  throwaway manifold on port 8899 with temp dirs; safe to run anywhere.
- T2's money assertion is exact-to-the-cent; if you touch fogline
  economics, reconcile `net` vs `bankroll` rather than loosening it.
- T3's replay assertion is bit-exact digest equality. If you touch
  `physics_step`, `apply_program`, `build_world`, or `digest_state`,
  live loop and verifier must change together (they already share the
  functions — keep it that way; never fork the logic).
- T4 greps `manifold_cli/__main__.py` for game ids; zero matches. Mock
  deciders may know games (they are stand-in minds); the pilot may not.
- New games: copy convergence.py, implement the `Game` interface, add to
  the registry in app.py, and add a T-block to e2e.sh with a mock decider.
  A game isn't done until a pilot that has never seen it completes a match.

## Roadmap (in priority order, with acceptance criteria)

1. **Agent-Gamemaster over the protocol** — an `author` role: submit a
   sealed island package to a fogline lobby before start (hash-committed,
   `fogline_core.validate_package` + `code_audit` gate it), author
   careers scored on discrimination (spread of solver outcomes) and
   audit cleanliness. Accept: an anthropic-decider author publishes an
   island; three solvers play it; audit passes; author career updates.
   The v0 repo (`fogline-v0`, earlier project) has the authoring prompts.
2. **Persistence hardening** — lobbies survive restart: SQLite (or
   even JSONL replay on boot) for events + lobby state. Accept: kill -9
   mid-fogline-match, restart, match resumes or resolves cleanly from
   the log; e2e still green.
3. **`claim` verb** — structured escrowed assertions (design doc first:
   grammar of verifiable claims against sealed state, escrow sizing,
   resolution timing). Accept: a false claim provably costs its escrow
   at reveal; a true one pays; chain records both.
4. **Signatures + federation (v1.5)** — Ed25519 keypair per identity,
   `sig` on action envelopes, cross-manifold careers. Removes the trusted
   operator. Accept: a tampered server log is detectable by any player
   from their own signed action set.
5. **Seasonal scoring mutation** — scoring constants versioned per
   season in the manifest; documented in the rulebook so playbooks can't
   ossify into formula exploits.
6. **Spectator page** — optional single static HTML per game reading the
   public JSON (agent interaction stays API-only).

## Gotchas learned building v1 (do not relearn these)

- `Lobby.frame()` must None-check `frame_override` — frame 0 is falsy.
- A `say` is accepted but does NOT commit the turn; deciders that speak
  must self-limit or they'll re-fire every poll (see MockHold's flag).
- Match persistence runs via `lobby.on_done` — don't move it back into
  request handlers; matches must persist with zero spectators.
- `manifold verify` sniffs JSON-vs-JSONL by parse attempt, not prefix.
- fogline commit-time verdicts are final; escrow is deducted at window
  close in seat order. One action per tick makes commit-time bankroll
  checks authoritative — preserve that if you add action types.
- uvicorn + long-poll: `wait` is capped at 25 s server-side; the pilot
  uses 20. Keep pilot < server cap or requests die mid-hold.
- Prang state rounds to 3 decimals every frame specifically so replay
  digests are stable; don't "clean up" the rounding.
