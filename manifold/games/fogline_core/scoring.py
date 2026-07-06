"""Deterministic scoring for Fogline v0.

The referee never uses judgment. Every number here is recomputable by anyone
holding the sealed package and the event log.

Stake anatomy:
    interval [lo, hi]   -- the claim: "truth lies in here"
    confidence c        -- self-reported P(hit), clamped to [0.05, 0.95]
    exposure X          -- doubloons at play, capped by tick (earliness = size)

Sharpness s = log2(domain_width / interval_width). Minimum 2.0 (your interval
must cover at most a QUARTER of the announced domain -- this keeps blind
wide staking ~breakeven net of ante), capped at 10.

Resolution:
    HIT  -> delta = +X * c * (1 + s/2)      conviction and precision pay
    MISS -> delta = -X * max(c^2, LOSS_FLOOR)  confident misses crater

Escrow at stake time = worst case = X * max(c^2, LOSS_FLOOR), so bankrolls
can never go negative mid-match.

Known, documented imperfection (see RULEBOOK.md): the money curve rewards
conviction slightly beyond calibration; the Brier rating is the counter-
pressure. Champions need both. The scoring rule itself is subject to
seasonal mutation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

C_MIN, C_MAX = 0.05, 0.95
S_MIN, S_MAX = 2.0, 10.0
LOSS_FLOOR = 0.15
MIN_EXPOSURE = 1.0

# Fraction of *liquid* bankroll you may expose per stake, by tick (1-indexed).
# Early conviction can move size; late stakes only nibble. This is the
# earliness premium.
TICK_EXPOSURE_CAP = [0.25, 0.20, 0.15, 0.10, 0.06, 0.03]


class StakeError(ValueError):
    """Raised when a stake violates the rules. Message is agent-readable."""


@dataclass(frozen=True)
class StakeTerms:
    """Referee-computed terms echoed back to the solver at commit time."""

    sharpness: float
    exposure: float
    escrow: float          # worst case loss, locked now
    max_win: float         # payout if hit
    tick_cap_fraction: float


def clamp_confidence(c: float) -> float:
    return max(C_MIN, min(C_MAX, float(c)))


def sharpness(domain: tuple[float, float], lo: float, hi: float) -> float:
    d_lo, d_hi = domain
    dom_w = d_hi - d_lo
    w = hi - lo
    if w <= 0:
        raise StakeError("interval width must be positive (hi > lo)")
    if dom_w <= 0:
        raise StakeError("island domain is degenerate; refuse this island")
    s = math.log2(dom_w / w)
    if s < S_MIN:
        raise StakeError(
            f"interval too wide: sharpness {s:.2f} < {S_MIN}. "
            f"Your interval must span at most a quarter of the domain "
            f"({dom_w / 4:.6g} wide)."
        )
    return min(s, S_MAX)


def validate_interval(domain: tuple[float, float], lo: float, hi: float) -> None:
    d_lo, d_hi = domain
    if lo < d_lo or hi > d_hi:
        raise StakeError(
            f"interval [{lo:.6g}, {hi:.6g}] leaves the announced domain "
            f"[{d_lo:.6g}, {d_hi:.6g}]"
        )


def stake_terms(
    domain: tuple[float, float],
    lo: float,
    hi: float,
    confidence: float,
    exposure: float,
    tick: int,
    liquid_bankroll: float,
) -> StakeTerms:
    """Validate a stake and compute its terms. Raises StakeError with an
    agent-readable message on any violation."""
    validate_interval(domain, lo, hi)
    s = sharpness(domain, lo, hi)
    c = clamp_confidence(confidence)

    if tick < 1 or tick > len(TICK_EXPOSURE_CAP):
        raise StakeError(f"tick {tick} outside island schedule")
    cap_frac = TICK_EXPOSURE_CAP[tick - 1]
    cap = cap_frac * liquid_bankroll

    x = float(exposure)
    if x < MIN_EXPOSURE:
        raise StakeError(f"exposure {x:.2f} below minimum {MIN_EXPOSURE}")
    if x > cap + 0.01:  # one-cent tolerance for rounding at the cap
        raise StakeError(
            f"exposure {x:.2f} exceeds tick-{tick} cap of {cap:.2f} db "
            f"({cap_frac:.0%} of your liquid {liquid_bankroll:.2f} db)"
        )

    escrow = x * max(c * c, LOSS_FLOOR)
    if escrow > liquid_bankroll + 1e-9:
        raise StakeError(
            f"worst-case loss {escrow:.2f} db exceeds liquid bankroll "
            f"{liquid_bankroll:.2f} db"
        )
    max_win = x * c * (1.0 + s / 2.0)
    return StakeTerms(
        sharpness=s,
        exposure=x,
        escrow=escrow,
        max_win=max_win,
        tick_cap_fraction=cap_frac,
    )


def resolve(terms: StakeTerms, confidence: float, hit: bool) -> float:
    """Doubloon delta for one stake at resolution."""
    c = clamp_confidence(confidence)
    if hit:
        return terms.exposure * c * (1.0 + terms.sharpness / 2.0)
    return -terms.exposure * max(c * c, LOSS_FLOOR)


def brier(outcomes: list[tuple[float, int]]) -> float | None:
    """Mean squared error of (confidence, hit) pairs. Lower is better.
    0.0 = oracle, 0.25 = coin-flip shrug, 1.0 = confidently wrong always."""
    if not outcomes:
        return None
    return sum((c - o) ** 2 for c, o in outcomes) / len(outcomes)
