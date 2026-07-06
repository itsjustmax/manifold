"""Island packages: the sealed artifact a Gamemaster authors.

A package contains the hidden truth, the full tick-by-tick clue schedule,
and the probe menu with pre-written answers. It is hash-committed at
surfacing (the seal) and revealed in full at resolution so anyone can
audit it. The referee only *executes* the package; it never invents.

Also home to the trivial seeded generator -- demoted from content engine
to test fixture, exactly as designed. Mock islands are internally
consistent by construction, which makes them useful for engine tests.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

SPEC_VERSION = "fogline-v0"
N_TICKS = 6


class PackageError(ValueError):
    pass


# ---------------------------------------------------------------- sealing

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal(package: dict) -> str:
    """SHA-256 over canonical JSON. Printed at surfacing, verified at reveal."""
    return hashlib.sha256(canonical_json(package).encode("utf-8")).hexdigest()


# ------------------------------------------------------------- validation

def validate_package(pkg: dict) -> list[str]:
    """Hard, code-only checks. Returns a list of violations (empty = valid)."""
    errs: list[str] = []
    if pkg.get("spec_version") != SPEC_VERSION:
        errs.append(f"spec_version must be '{SPEC_VERSION}'")
    isl = pkg.get("island")
    if not isinstance(isl, dict):
        return errs + ["missing island object"]

    name = isl.get("name")
    if not isinstance(name, str) or not (2 <= len(name) <= 80):
        errs.append("island.name must be a 2-80 char string")

    truth = isl.get("truth", {})
    if truth.get("kind") != "numeric":
        errs.append("v0 supports truth.kind == 'numeric' only")
    q = truth.get("question")
    if not isinstance(q, str) or len(q) < 10:
        errs.append("truth.question must be a descriptive string")
    dom = truth.get("domain")
    val = truth.get("value")
    try:
        d_lo, d_hi = float(dom[0]), float(dom[1])
        v = float(val)
        if not d_lo < d_hi:
            errs.append("domain must satisfy lo < hi")
        elif d_hi / max(abs(d_lo), 1e-9) < 5 and (d_hi - d_lo) < 4 * max(abs(d_lo), 1.0):
            # Soft heuristic: demand a real search space.
            errs.append("domain too tight: give solvers a genuine search space")
        if not (d_lo <= v <= d_hi):
            errs.append("truth.value must lie inside domain")
    except (TypeError, ValueError, IndexError, KeyError):
        errs.append("truth.domain must be [lo, hi] numbers and truth.value numeric")

    ticks = isl.get("ticks")
    if not isinstance(ticks, list) or len(ticks) != N_TICKS:
        errs.append(f"exactly {N_TICKS} ticks required")
    else:
        for i, t in enumerate(ticks, 1):
            if not isinstance(t, dict) or t.get("tick") != i:
                errs.append(f"tick {i}: must be an object with tick=={i}")
            clue = (t or {}).get("clue")
            if not isinstance(clue, str) or not (10 <= len(clue) <= 600):
                errs.append(f"tick {i}: clue must be a 10-600 char string")

    probes = isl.get("probes", [])
    if not isinstance(probes, list) or not (0 <= len(probes) <= 6):
        errs.append("probes must be a list of 0-6 entries")
    else:
        seen = set()
        for p in probes:
            pid = (p or {}).get("id")
            if not isinstance(pid, str) or not pid or pid in seen:
                errs.append("each probe needs a unique string id")
                continue
            seen.add(pid)
            cost = p.get("cost")
            if not isinstance(cost, (int, float)) or not (5 <= cost <= 200):
                errs.append(f"probe '{pid}': cost must be 5-200 db")
            ans = p.get("answer")
            if not isinstance(ans, str) or not (5 <= len(ans) <= 400):
                errs.append(f"probe '{pid}': answer must be a 5-400 char string")
            aft = p.get("available_from_tick", 1)
            if not isinstance(aft, int) or not (1 <= aft <= N_TICKS):
                errs.append(f"probe '{pid}': available_from_tick must be 1-{N_TICKS}")

    diff = isl.get("author_difficulty_estimate")
    if not isinstance(diff, (int, float)) or not (0.0 <= diff <= 1.0):
        errs.append("author_difficulty_estimate must be in [0,1]")

    return errs


def public_announcement(pkg: dict) -> dict:
    """What solvers see at surfacing: everything except truth.value,
    the clue texts, and probe answers."""
    isl = pkg["island"]
    return {
        "spec_version": pkg["spec_version"],
        "island": {
            "name": isl["name"],
            "flavor": isl.get("flavor", ""),
            "question": isl["truth"]["question"],
            "units": isl["truth"].get("units", ""),
            "domain": isl["truth"]["domain"],
            "n_ticks": N_TICKS,
            "probes": [
                {
                    "id": p["id"],
                    "cost": p["cost"],
                    "available_from_tick": p.get("available_from_tick", 1),
                    "teaser": p.get("teaser", ""),
                }
                for p in isl.get("probes", [])
            ],
        },
    }


def code_audit(pkg: dict) -> dict:
    """Post-reveal hard checks. Consistency of prose clues with the truth is
    an *advisory* LLM audit elsewhere; this is the deterministic part."""
    errs = validate_package(pkg)
    isl = pkg["island"]
    v = float(isl["truth"]["value"])
    d_lo, d_hi = (float(x) for x in isl["truth"]["domain"])
    checks = {
        "schema_valid": not errs,
        "schema_errors": errs,
        "truth_in_domain": d_lo <= v <= d_hi,
        "truth_not_on_edge": (v - d_lo) > 0.01 * (d_hi - d_lo)
        and (d_hi - v) > 0.01 * (d_hi - d_lo),
    }
    checks["pass"] = checks["schema_valid"] and checks["truth_in_domain"]
    return checks


# ------------------------------------------------- trivial seeded generator

_NAMES = [
    ("Saltmere Atoll", "a ring of salt pans and cisterns in a jade lagoon"),
    ("Gullwrack Shoals", "wind-scoured stacks where longliners shelter"),
    ("Pellucid Bight", "a drowned caldera rim with black-sand quays"),
    ("Brinehollow", "terraced kelp farms under basalt cliffs"),
]


def make_mock_package(seed: int) -> dict:
    """Internally consistent numeric island, derived from one hidden value.

    Includes a mock-only `_mock_hints` block that scripted solvers may read.
    LLM solvers never see the package -- only the referee's public views --
    so the hints leak nothing in real play.
    """
    rng = random.Random(seed)
    name, flavor = _NAMES[seed % len(_NAMES)]
    pop = rng.randint(800, 60_000)
    d_lo, d_hi = 100, 100_000

    boats = max(3, round(pop / rng.uniform(90, 160)))
    schools = max(1, round(pop / rng.uniform(1200, 2000)))
    harbor_share = rng.uniform(0.18, 0.32)
    harbor_census = round(pop * harbor_share / 10) * 10
    barques = max(1, round(pop / 2500))

    pkg = {
        "spec_version": SPEC_VERSION,
        "island": {
            "name": name,
            "flavor": flavor,
            "truth": {
                "kind": "numeric",
                "question": f"What is the population of {name}?",
                "value": pop,
                "units": "people",
                "domain": [d_lo, d_hi],
            },
            "ticks": [
                {"tick": 1, "clue": f"Aerial sketch: {flavor}; roughly "
                                    f"{rng.randint(9, 22)} km of shoreline, no peaks."},
                {"tick": 2, "clue": f"Fishing registry fragment: {boats} licensed boats."},
                {"tick": 3, "clue": "Salt pans in the lagoon; freshwater from cisterns only."},
                {"tick": 4, "clue": f"Missionary letter: {schools} schoolhouse(s), each crowded past capacity."},
                {"tick": 5, "clue": f"Port census: {harbor_census} souls in the harbor district alone."},
                {"tick": 6, "clue": "The fog thins to nothing. Final stakes close."},
            ],
            "probes": [
                {
                    "id": "harbormaster_note",
                    "cost": 40,
                    "teaser": "A page from the harbormaster's ledger.",
                    "answer": f"{barques} salt barque(s) loaded weekly for the mainland.",
                    "available_from_tick": 1,
                },
                {
                    "id": "parish_rolls",
                    "cost": 65,
                    "teaser": "The parish keeps birth rolls.",
                    "answer": f"Roughly {round(pop * 0.021)} births recorded last year.",
                    "available_from_tick": 3,
                },
            ],
            "author_difficulty_estimate": 0.45,
            "_mock_hints": {
                "boats": boats,
                "schools": schools,
                "harbor_census": harbor_census,
                "harbor_share": round(harbor_share, 3),
            },
        },
    }
    return pkg
