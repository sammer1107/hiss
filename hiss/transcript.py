"""The per-session log of every snippet and its full output; it outlives the session."""
from __future__ import annotations

import threading
import time
from pathlib import Path


def _clock(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


class Transcript:
    def __init__(self, path: Path):
        self._file = open(path, "a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def note(self, text: str) -> None:
        self._write(f"\n=== {_clock(time.time())} {text} ===\n")

    def run(self, n: int, started: float, status: str, elapsed: float, code: str, output: str) -> None:
        lines = code.rstrip("\n").split("\n") or [""]
        shown = [">>> " + lines[0]] + ["... " + line for line in lines[1:]]
        body = output if not output or output.endswith("\n") else output + "\n"
        self._write(f"\n--- #{n} {_clock(started)} {status} {elapsed:.2f}s\n" + "\n".join(shown) + "\n" + body)

    def _write(self, text: str) -> None:
        with self._lock:
            self._file.write(text)
            self._file.flush()
