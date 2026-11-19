"""The hiss command line."""
from __future__ import annotations

import argparse
import os
import random
import signal
import string
import subprocess
import sys
import time

from .cleanup import prune
from .client import NoSession, Unresponsive, read_state, request
from .paths import HOME, NAME_RE, RUN_PY, SessionPaths, all_sessions

DEFAULT_TIMEOUT = 60      # seconds `run` / `wait` block before answering "still running"
DEFAULT_MAX = 20000       # characters of output shown before the middle is cut
DEFAULT_IDLE = 10         # minutes without requests before a session exits
INTERRUPT_GRACE = 5       # seconds `interrupt` waits for the snippet to stop
START_WAIT = 15           # seconds `start` waits for the session to come up
REPLY_MARGIN = 30         # seconds an answer may take beyond the wait it was asked for
MAX_PREFIX = 40           # characters of the name given to `start`, before the -xxxx suffix
SUFFIX_CHARS = string.ascii_lowercase + string.digits

EXIT_OK, EXIT_ERROR, EXIT_RUNNING, EXIT_HISS = 0, 1, 2, 3

USAGE = """\
hiss: Headless Interactive Session Server. Persistent Python REPL sessions, one per name.

  hiss start NAME [--python PATH] [--idle MIN]   start a session in the current directory;
                                                 prints its handle, NAME-xxxx, used by the rest
  hiss run NAME [--timeout S] [--max N] <<'EOF'  run a snippet (or -c CODE) and print its output
  hiss wait NAME [--timeout S] [--max N]         more output from a snippet still running
  hiss interrupt NAME                            Ctrl+C the running snippet; the session survives
  hiss stop NAME                                 end the session
  hiss list                                      running sessions
  hiss log NAME [--max N]                        the session's transcript

A snippet runs like a block pasted into the Python REPL: only the last statement
echoes its value, and the first error stops the rest. Exit codes: 0 ok, 1 the
snippet raised, 2 still running, 3 hiss itself failed (no session, busy, ...).
"""


class Fail(Exception):
    """A hiss-level problem: printed as `hiss: <message>`, exit code 3."""


def main() -> None:
    if sys.argv[1:2] == ["_serve"]:  # the session process itself, spawned by `start`
        from .server import serve
        serve(sys.argv[2], float(sys.argv[3]))
        return
    args = _parser().parse_args()
    try:
        code = args.command(args)
    except Fail as e:
        print(f"hiss: {e}", file=sys.stderr)
        code = EXIT_HISS
    sys.exit(code)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hiss", usage=USAGE, add_help=True)
    sub = parser.add_subparsers(required=True, metavar="COMMAND")

    def command(name, func, with_name=True):
        p = sub.add_parser(name)
        p.set_defaults(command=func)
        if with_name:
            p.add_argument("name")
        return p

    p = command("start", start)
    p.add_argument("--python", default=sys.executable, help="interpreter to run the session with, e.g. a venv's")
    p.add_argument("--idle", type=float, default=DEFAULT_IDLE, help="minutes without requests before it exits")
    p = command("run", run)
    p.add_argument("-c", dest="code", help="the snippet; read from stdin when omitted")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--max", type=int, default=DEFAULT_MAX, help="0 shows everything")
    p = command("wait", wait)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p = command("interrupt", interrupt)
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    command("stop", stop)
    command("list", list_sessions, with_name=False)
    p = command("log", log)
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    return parser


# --- commands

def start(args) -> int:
    prune()
    paths = _claim(args.name)
    paths.out_dir.mkdir()
    with open(paths.console, "wb") as console:
        proc = _spawn([args.python, str(RUN_PY), "_serve", paths.name, str(args.idle)], console)
    deadline = time.time() + START_WAIT
    while (state := read_state(paths)) is None:
        if proc.poll() is not None or time.time() > deadline:
            output = paths.console.read_text(encoding="utf-8", errors="replace").strip()
            raise Fail(f"session '{paths.name}' failed to start" + (f":\n{output}" if output else ""))
        time.sleep(0.05)
    print(f"hiss: started session {paths.name} (python {state['version']}, pid {state['pid']}, "
          f"cwd {state['cwd']}, exits after {args.idle:g} min idle)")
    return EXIT_OK


def run(args) -> int:
    code = args.code if args.code is not None else _read_stdin()
    reply = _call(args.name, "run", args.timeout, code=code, timeout=args.timeout)
    return _show(args.name, reply, args.max)


def wait(args) -> int:
    reply = _call(args.name, "wait", args.timeout, timeout=args.timeout)
    return _show(args.name, reply, args.max)


def interrupt(args) -> int:
    reply = _call(args.name, "interrupt", INTERRUPT_GRACE, timeout=INTERRUPT_GRACE)
    code = _show(args.name, reply, args.max)
    if code == EXIT_RUNNING:
        print(f"hiss: Ctrl+C didn't stop it within {INTERRUPT_GRACE}s (probably blocked inside C code); "
              f"`hiss stop {args.name}` ends the session", file=sys.stderr)
    return code


def stop(args) -> int:
    paths = _paths(args.name)
    state = read_state(paths)
    if state is None:
        raise Fail(f"no session '{args.name}'")
    try:
        request(paths, "stop", 10)
    except NoSession:
        # Nothing answers on its port: the process is gone. Its pid may already belong
        # to an unrelated process, so never kill by pid here.
        paths.state.unlink(missing_ok=True)
        raise Fail(f"'{args.name}' was not running (it had died); transcript: {paths.transcript}") from None
    except Unresponsive:
        # It accepted the connection, so the process is alive and the pid is still its own.
        try:
            os.kill(state["pid"], signal.SIGTERM)
        except OSError:
            pass
        paths.state.unlink(missing_ok=True)
    print(f"hiss: stopped '{args.name}'; transcript: {paths.transcript}")
    return EXIT_OK


def list_sessions(args) -> int:
    rows = []
    for paths in all_sessions():
        state = read_state(paths)
        if state is None:
            continue
        try:
            st = request(paths, "status", 3)
        except NoSession:
            continue
        except Unresponsive:  # alive, but user code is holding the interpreter
            rows.append((paths.name, "unresponsive", "? runs", f"python {state['version']}", state["cwd"]))
            continue
        doing = f"busy #{st['busy']} {_duration(st['busy_for'])}" if st["busy"] else f"idle {_duration(st['idle_for'])}"
        rows.append((paths.name, doing, f"{st['runs']} runs", f"python {state['version']}", state["cwd"]))
    if not rows:
        print("hiss: no sessions running")
        return EXIT_OK
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)) + "  " + row[-1])
    return EXIT_OK


def log(args) -> int:
    paths = _paths(args.name)
    if not paths.transcript.exists():
        raise Fail(f"no transcript for '{args.name}'")
    text = paths.transcript.read_text(encoding="utf-8", errors="replace")
    _write(f"transcript: {paths.transcript}\n" + _clip(text, args.max, str(paths.transcript)))
    return EXIT_OK


# --- helpers

def _paths(name: str) -> SessionPaths:
    if not NAME_RE.match(name):
        raise Fail(f"bad session name {name!r}: use letters, digits, '_', '-', '.'")
    return SessionPaths(name)


def _claim(prefix: str) -> SessionPaths:
    """Creates a new session folder under a unique handle, PREFIX-xxxx.

    Every start gets its own handle, so agents that pick the same name never share,
    stop or overwrite each other's sessions; mkdir is atomic, so two starts racing
    can't claim the same handle either.
    """
    if not NAME_RE.match(prefix) or len(prefix) > MAX_PREFIX:
        raise Fail(f"bad session name {prefix!r}: up to {MAX_PREFIX} letters, digits, '_', '-', '.'")
    HOME.mkdir(parents=True, exist_ok=True)
    while True:
        paths = SessionPaths(f"{prefix}-{''.join(random.choices(SUFFIX_CHARS, k=4))}")
        try:
            paths.dir.mkdir()
            return paths
        except FileExistsError:
            continue


def _spawn(cmd: list[str], console) -> subprocess.Popen:
    """Starts the session process detached, so it outlives the shell call that started it."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    kwargs = dict(cwd=os.getcwd(), stdin=subprocess.DEVNULL, stdout=console, stderr=console, env=env)
    if os.name != "nt":
        return subprocess.Popen(cmd, start_new_session=True, **kwargs)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Leave the caller's job object too, or a tool runner that kills its job kills the session.
        return subprocess.Popen(cmd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:  # the job doesn't allow breaking away
        return subprocess.Popen(cmd, creationflags=flags, **kwargs)


def _call(name: str, op: str, wait: float, **fields) -> dict:
    paths = _paths(name)
    try:
        reply = request(paths, op, wait + REPLY_MARGIN, **fields)
    except Unresponsive:
        raise Fail(f"'{name}' didn't answer; `hiss stop {name}` ends it") from None
    except NoSession:
        paths.state.unlink(missing_ok=True)  # stale if the process died
        message = f"no session '{name}'"
        if paths.transcript.exists():
            message += f" (it exited, was stopped or died; transcript: {paths.transcript})"
        raise Fail(f"{message}; start a new one with `hiss start {name.rsplit('-', 1)[0]}`") from None
    if reply.get("error") == "busy":
        raise Fail(f"'{name}' is busy with #{reply['n']} ({_duration(reply['elapsed'])}); "
                   f"`hiss wait {name}` for its output, `hiss interrupt {name}` to stop it")
    if "error" in reply:
        raise Fail(reply["error"])
    return reply


def _show(name: str, reply: dict, max_chars: int) -> int:
    status = reply["status"]
    running = status == "running"
    where = reply["out_file"] + (" once the run finishes" if running else "")
    _write(_clip(reply["output"], max_chars, where))
    if running:
        line = (f"[hiss {name} #{reply['n']} still running {_duration(reply['elapsed'])}: "
                f"`hiss wait {name}` for more, `hiss interrupt {name}` to stop it]")
    else:
        line = f"[hiss {name} #{reply['n']} {status} {reply['elapsed']:.2f}s]"
    print(line, file=sys.stderr)
    return {"ok": EXIT_OK, "running": EXIT_RUNNING}.get(status, EXIT_ERROR)


def _clip(text: str, max_chars: int, where: str) -> str:
    if not max_chars or len(text) <= max_chars:
        return text
    half = max_chars // 2
    head, tail = text[:half], text[-half:]
    # Cut on line boundaries when the lines are short enough to allow it.
    if "\n" in head:
        head = head[:head.rindex("\n") + 1]
    if "\n" in tail[:-1]:
        tail = tail[tail.index("\n") + 1:]
    cut = len(text) - len(head) - len(tail)
    return f"{head.rstrip(chr(10))}\n[... {cut} characters cut; full text: {where} ...]\n{tail}"


def _write(text: str) -> None:
    if text and not text.endswith("\n"):
        text += "\n"
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.flush()


def _read_stdin() -> str:
    if sys.stdin is None or sys.stdin.isatty():
        raise Fail("give the snippet on stdin (heredoc) or with -c")
    return sys.stdin.buffer.read().decode("utf-8")


def _duration(seconds: float) -> str:
    return f"{seconds:.0f}s" if seconds < 120 else f"{seconds / 60:.1f}m"
