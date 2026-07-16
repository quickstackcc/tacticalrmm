from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .rules import new_state


def load_state(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return new_state()
    with p.open() as f:
        loaded = json.load(f)
    base = new_state()
    base.update(loaded)
    return base


def save_state(path: str, state: dict[str, Any]) -> None:
    """Atomic write: state survives a crash mid-save."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tripwire-state.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, p)
    except BaseException:
        os.unlink(tmp)
        raise
