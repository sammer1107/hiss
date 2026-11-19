"""Where a session keeps its files."""
from __future__ import annotations

import os
import re
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
RUN_PY = TOOL_ROOT / "run.py"
HOME = Path(os.environ.get("HISS_HOME") or TOOL_ROOT / "sessions")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# What `start` hands out: NAME-xxxx. No command name looks like this.
HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]+-[a-z0-9]{4}$")


class SessionPaths:
    def __init__(self, name: str):
        self.name = name
        self.dir = HOME / name
        self.state = self.dir / "state.json"            # port, token, pid: exists while the session lives
        self.console = self.dir / "console.txt"         # the session's fd 1 and 2: everything it prints
        self.transcript = self.dir / "transcript.log"   # every snippet and its output; kept after the session ends
        self.out_dir = self.dir / "out"                 # full output of each run, by run number

    def out(self, n: int) -> Path:
        return self.out_dir / f"{n}.txt"


def all_sessions() -> list[SessionPaths]:
    if not HOME.is_dir():
        return []
    return [SessionPaths(p.name) for p in sorted(HOME.iterdir()) if p.is_dir()]
