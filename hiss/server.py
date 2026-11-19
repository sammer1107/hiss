"""The session process.

User code runs on the main thread, so Ctrl+C and libraries that need the main
thread (signal handlers) behave as in the REPL. Requests are answered on socket
threads, which is how `wait`, `interrupt` and `stop` reach a session that is busy.
"""
from __future__ import annotations

import builtins
import json
import os
import queue
import secrets
import signal
import socketserver
import sys
import threading
import time
import types

from .console import ConsoleReader, FdWriter, route_child_output
from .paths import SessionPaths
from .repl import run_source
from .transcript import Transcript

# Output of a snippet still running is returned up to its last newline, so a line
# isn't split across two answers, unless the unfinished line is longer than this.
PARTIAL_LINE_LIMIT = 4096


class Run:
    def __init__(self, n: int, code: str, start: int):
        self.n = n
        self.code = code
        self.start = start      # console.txt offset where its output begins
        self.cursor = start     # how far its output has been handed out
        self.end: int | None = None  # set when it finishes
        self.status = "running"
        self.started = time.time()
        self.finished: float | None = None
        self.done = threading.Event()

    @property
    def running(self) -> bool:
        return self.end is None

    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


class Session:
    def __init__(self, paths: SessionPaths, idle_seconds: float):
        self.paths = paths
        self.idle_seconds = idle_seconds
        self.console = ConsoleReader(paths.console)
        self.transcript = Transcript(paths.transcript)
        self.jobs: queue.Queue[Run] = queue.Queue()
        self.lock = threading.Lock()
        self.current: Run | None = None
        self.count = 0
        self.last_active = time.time()
        # A fresh __main__, as in the REPL; registered so pickle finds classes defined in it.
        main = types.ModuleType("__main__")
        main.__builtins__ = builtins
        sys.modules["__main__"] = main
        self.namespace = main.__dict__

    # --- main thread

    def execute_forever(self) -> None:
        run = None
        while True:
            try:
                run = self.jobs.get()
                self.execute(run)
            except KeyboardInterrupt:  # landed between runs or while recording one
                if run is not None and run.running:
                    self.finish(run, "interrupted")
            run = None

    def execute(self, run: Run) -> None:
        status = "interrupted"
        try:
            status = run_source(run.code, self.namespace, f"<hiss#{run.n}>")
        except KeyboardInterrupt:  # landed between statements, outside user code
            sys.stderr.write("KeyboardInterrupt\n")
        self.finish(run, status)

    def finish(self, run: Run, status: str) -> None:
        for stream in (sys.__stdout__, sys.__stderr__):
            if stream is not None:
                stream.flush()
        end = self.console.size()
        output = self.console.read(run.start, end)
        self.paths.out(run.n).write_bytes(output)
        run.finished = time.time()
        self.transcript.run(run.n, run.started, status, run.elapsed(), run.code, output.decode("utf-8", "replace"))
        with self.lock:
            run.status = status
            run.end = end
            self.last_active = time.time()
        run.done.set()

    # --- request threads

    def handle(self, req: dict) -> dict:
        op = req.get("op")
        if op == "status":
            return self.status()
        with self.lock:
            self.last_active = time.time()
        if op == "run":
            return self.start_run(req["code"], req["timeout"])
        if op == "wait":
            return self.wait(req["timeout"])
        if op == "interrupt":
            return self.interrupt(req["timeout"])
        return {"error": f"unknown request {op!r}"}

    def start_run(self, code: str, timeout: float) -> dict:
        with self.lock:
            busy = self.current
            if busy is not None and busy.running:
                return {"error": "busy", "n": busy.n, "elapsed": busy.elapsed()}
            self.count += 1
            run = self.current = Run(self.count, code, self.console.size())
        self.jobs.put(run)
        return self.collect(run, timeout)

    def wait(self, timeout: float) -> dict:
        run = self.current
        if run is None:
            return {"error": "nothing has run yet"}
        return self.collect(run, timeout)

    def interrupt(self, timeout: float) -> dict:
        with self.lock:
            run = self.current
            if run is None or not run.running:
                return {"error": "not running anything"}
            signal.raise_signal(signal.SIGINT)  # unlike _thread.interrupt_main, also wakes time.sleep
        return self.collect(run, timeout)

    def collect(self, run: Run, timeout: float) -> dict:
        """Waits up to `timeout` for the run to finish, then hands out its output not yet handed out."""
        run.done.wait(timeout)
        with self.lock:
            finished = not run.running
            data = self.console.read(run.cursor, run.end if finished else self.console.size())
            if not finished:
                cut = data.rfind(b"\n") + 1
                if cut or len(data) <= PARTIAL_LINE_LIMIT:
                    data = data[:cut]
            run.cursor += len(data)
            return {
                "n": run.n,
                "status": run.status,
                "elapsed": run.elapsed(),
                "output": data.decode("utf-8", "replace"),
                "out_file": str(self.paths.out(run.n)),
            }

    def status(self) -> dict:
        with self.lock:
            run = self.current
            busy = run is not None and run.running
            return {
                "pid": os.getpid(),
                "runs": self.count,
                "busy": run.n if busy else None,
                "busy_for": run.elapsed() if busy else None,
                "idle_for": None if busy else time.time() - self.last_active,
            }

    def watch_idle(self) -> None:
        while True:
            time.sleep(5)
            with self.lock:
                run = self.current
                idle = (run is None or not run.running) and time.time() - self.last_active > self.idle_seconds
            if idle:
                self.exit(f"exited after {self.idle_seconds / 60:g} min idle")

    def exit(self, reason: str) -> None:
        run = self.current
        if run is not None and run.running:
            output = self.console.read(run.start, self.console.size())
            self.transcript.run(run.n, run.started, "killed", run.elapsed(), run.code, output.decode("utf-8", "replace"))
        self.transcript.note(reason)
        try:
            self.paths.state.unlink()
        except OSError:
            pass
        os._exit(0)


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self) -> None:
        try:
            req = json.loads(self.rfile.readline())
        except ValueError:
            return
        if not secrets.compare_digest(str(req.get("token", "")), self.server.token):
            return
        session = self.server.session
        if req.get("op") == "stop":
            self._reply({"status": "stopped"})
            session.exit("stopped")
        try:
            reply = session.handle(req)
        except Exception as e:  # never let socketserver print into the console
            reply = {"error": f"hiss internal error: {e!r}"}
        self._reply(reply)

    def _reply(self, reply: dict) -> None:
        self.wfile.write((json.dumps(reply) + "\n").encode("utf-8"))


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    session: Session
    token: str

    def handle_error(self, request, client_address) -> None:
        pass  # the default prints a traceback into the console, i.e. into user output


def serve(name: str, idle_minutes: float) -> None:
    paths = SessionPaths(name)
    sys.stdout = sys.stderr = FdWriter(1)  # fd 1 and 2 are both console.txt
    route_child_output()
    # A process started from a background shell job inherits SIGINT as ignored (POSIX),
    # and Python then installs no handler, so `interrupt` would do nothing.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    session = Session(paths, idle_minutes * 60)
    server = _Server(("127.0.0.1", 0), _Handler)
    server.session = session
    server.token = secrets.token_hex(16)
    version = sys.version.split()[0]
    state = {
        "pid": os.getpid(),
        "port": server.server_address[1],
        "token": server.token,
        "python": sys.executable,
        "version": version,
        "cwd": os.getcwd(),
        "started": time.time(),
        "idle_minutes": idle_minutes,
    }
    tmp = paths.state.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, paths.state)
    session.transcript.note(f"started: python {version} ({sys.executable}), cwd {os.getcwd()}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=session.watch_idle, daemon=True).start()
    # From here on the process should look like a fresh REPL to user code.
    sys.argv = [""]
    sys.path[0] = ""
    session.execute_forever()
