"""Where a manifold keeps its durable state (careers, match logs,
mesh directory, live journals). Stdlib-only: serve.py imports this
before any dependency exists."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """MANIFOLD_DATA wins; HARBOR_DATA honored for pre-rename deploys;
    otherwise ./manifold_data, falling back to an existing ./harbor_data
    so a rename never orphans careers."""
    env = os.environ.get("MANIFOLD_DATA") or os.environ.get("HARBOR_DATA")
    if env:
        p = Path(env)
    else:
        p = Path("manifold_data")
        legacy = Path("harbor_data")
        if not p.exists() and legacy.exists():
            p = legacy
    p.mkdir(parents=True, exist_ok=True)
    return p
