"""Talking to a session process: one JSON line out, one JSON line back, per connection."""
from __future__ import annotations

import json
import socket

from .paths import SessionPaths


class NoSession(Exception):
    """The session isn't running, or died while answering."""


class Unresponsive(Exception):
    """The session accepted the request but didn't answer in time."""


def read_state(paths: SessionPaths) -> dict | None:
    try:
        return json.loads(paths.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def request(paths: SessionPaths, op: str, wait: float = 10, **fields) -> dict:
    """Sends one request; `wait` bounds how long the answer may take."""
    state = read_state(paths)
    if state is None:
        raise NoSession()
    try:
        sock = socket.create_connection(("127.0.0.1", state["port"]), timeout=5)
    except OSError as e:
        raise NoSession() from e
    with sock:
        sock.settimeout(wait)
        try:
            sock.sendall((json.dumps({"token": state["token"], "op": op, **fields}) + "\n").encode("utf-8"))
            line = sock.makefile("rb").readline()
        except socket.timeout as e:
            raise Unresponsive() from e
        except OSError as e:
            raise NoSession() from e
    if not line:
        raise NoSession()
    return json.loads(line)
