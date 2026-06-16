from __future__ import annotations

import os
from pathlib import Path


def beads_subprocess_env(password_file: Path) -> dict[str, str]:
    env = os.environ.copy()
    if env.get("BEADS_DOLT_PASSWORD"):
        return env
    try:
        password = password_file.read_text(encoding="utf-8").rstrip("\n")
    except FileNotFoundError:
        return env
    if password:
        env["BEADS_DOLT_PASSWORD"] = password
    return env
