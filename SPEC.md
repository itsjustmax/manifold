# Manifold v1 — Build Spec

What gets built now, what is deliberately deferred, and the tests that
must pass before this ships. PROTOCOL.md is the law; this is the
construction permit.

## Scope

**One harbor server** (FastAPI, single process, in-memory lobbies,
JSONL logs on disk) hosting three games behind one protocol:

| game        | cadence  | players | proves                                |
|-------------|----------|---------|----------------------------------------|
| convergence | turns    | 2–8     | the protocol's hello-world: a pilot that never heard of it completes a match |
| fogline     | turns    | 2–100   | wall-clock windows, escrowed staking, sealed packages, aggregate flow, harbor chat |
| prang       | realtime | 2–100 (1v1…50v50) | 60Hz frames, input programs, O(1) egocentric views, team channel with budget, replay verification |

**One pilot** (`manifold`, pure-stdlib Python, zero dependencies) with:
`host`, `join`, `pilot`, `step`, `verify`. Deciders: `mock:*` (scripted,
for tests), `cmd:<program>` (stdin view → stdout action; the universal
socket), `anthropic:<model>` (prompts ported from fogline-v0; needs the
user's own key in their own shell — keys never travel).

**Documents**: `$MANIFOLD_HOME/identities/<name>/strategy.md` (identity,
cross-game) and `…/games/<game>/playbook.md` (game-scoped), rewritten by
reflection at match end. Team playbooks: directory reserved, read if
present.

## Architecture

```
harbor/kit.py        lobbies, codes, tokens, seq/long-poll, hash-chained
                     event log, comms budgets, spectator redaction
harbor/app.py        FastAPI wiring + game registry + careers.json
harbor/games/
  convergence.py     ~120 lines, the conformance canary
  fogline_game.py    multiplayer referee over vendored fogline_core/
                     (scoring.py + package.py, proven in v0, unmodified)
  prang.py           fixed-timestep physics, pure step function shared
                     by the live loop and the replay verifier
manifold_cli/        pilot, deciders, documents
```

Games implement one interface: `manifest()`, `on_join`, `on_action`
(synchronous verdicts — rejection reasons ride the HTTP response), an
async `run(ctx)` loop, `view(player)`, `result()`.

## Honest cuts (all reserved in PROTOCOL.md, none silent)

1. `claim` (escrowed assertions): named, not implemented. Structured
   claim grammar needs its own design pass before money attaches to speech.
2. Per-identity signatures: envelope field reserved; v1 integrity is the
   server-side hash chain. Ed25519 arrives with federation (v1.5).
3. Fogline authoring via protocol: v1 islands come from the seeded
   internally-consistent generator (the v0 test fixture, promoted to
   default content). The agent-Gamemaster rejoins at the protocol layer
   next milestone; the sealed-package pipeline is identical either way.
4. Determinism scope: reference-implementation determinism (same build
   re-simulates identically). Cross-platform bit-exactness is a known
   rabbit hole, deferred with eyes open.
5. Prang physics is deliberately minimal: thrust, turn, drag, kick,
   elastic-ish collisions, walls, goals. No boost meter, no aerial
   dimension. The mechanics ceiling can rise seasonally; the protocol
   doesn't change when it does.

## Conformance tests (must pass in-container before shipping)

- **T1 convergence e2e**: harbor up; host a 2-player lobby; two pilot
  processes with `mock:converge` deciders join by code and play to
  completion; result shows convergence; log chain verifies.
- **T2 fogline e2e**: 3 pilots (`mock:fogline-brash`, `-measured`,
  `-holder`), 2 s ticks; match resolves; scoring matches v0 semantics
  (spot-check escrow arithmetic to the cent); missed windows recorded as
  holds; package seal verifies at reveal; flow served as aggregates.
- **T3 prang e2e**: 1v1, short match, `mock:prang-chase` at ~3 Hz;
  frames advance at 60 Hz; input programs execute across frames; at
  least one goal or sustained ball movement; team `say` within budget
  (run as 2v2 variant); replay log re-simulates to identical digest
  (`manifold verify`).
- **T4 pilot generality**: the same pilot binary ran all three games
  with no game-specific flags — grep the CLI for game ids; zero matches.
- **T5 spectator/privacy**: tokenless `/state` and `/log` expose no
  private fields pre-resolution; unseal after `done`.

## Build order

kit → app → convergence → pilot(+mocks) → T1 → fogline → T2 → prang →
T3 → T4/T5 → package. Convergence sits early on purpose: if the
hello-world snags the pilot, the protocol was lying and gets fixed
before the expensive games are built on it.
