"""Removing what dead sessions leave behind; runs on every `start`.

A dead session's console.txt and out/ go right away: the transcript holds every
snippet's full output, so they only duplicate it. The whole folder, transcript
included, goes once the transcript is KEEP_DAYS old.
"""
from __future__ import annotations

import shutil
import time

from .client import NoSession, Unresponsive, request
from .paths import SessionPaths, all_sessions

KEEP_DAYS = 7
# A folder without state.json this new may belong to a session still starting up.
STARTUP_GRACE = 120


def prune() -> None:
    now = time.time()
    for paths in all_sessions():
        if not _dead(paths, now):
            continue
        if now - _last_change(paths) > KEEP_DAYS * 86400:
            shutil.rmtree(paths.dir, ignore_errors=True)
        else:
            try:
                paths.console.unlink(missing_ok=True)
            except OSError:  # still held open by a process that won't die; next time
                pass
            shutil.rmtree(paths.out_dir, ignore_errors=True)


def _dead(paths: SessionPaths, now: float) -> bool:
    if paths.state.exists():
        try:
            request(paths, "status", 3)
            return False
        except Unresponsive:  # alive, just slow
            return False
        except NoSession:  # crashed: its state.json was never removed
            paths.state.unlink(missing_ok=True)
            return True
    try:
        return now - paths.dir.stat().st_mtime > STARTUP_GRACE
    except OSError:
        return False


def _last_change(paths: SessionPaths) -> float:
    target = paths.transcript if paths.transcript.exists() else paths.dir
    return target.stat().st_mtime
