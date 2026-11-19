"""The session's output: fd 1 and 2 both point at console.txt, so prints, tracebacks,
C extensions and child processes all land there in order. A run's output is the
byte range the file grew by while it ran."""
from __future__ import annotations

import io
import os
import threading
from pathlib import Path


class _FdBinary:
    """sys.stdout.buffer for code that writes bytes."""

    def __init__(self, fd: int):
        self._fd = fd

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        while view:
            view = view[os.write(self._fd, view):]
        return len(data)

    def flush(self) -> None:
        pass


class FdWriter(io.TextIOBase):
    """Unbuffered text stream on a file descriptor; used for both sys.stdout and sys.stderr,
    so their output interleaves exactly as written."""

    encoding = "utf-8"
    errors = "backslashreplace"

    def __init__(self, fd: int):
        self._fd = fd
        self.buffer = _FdBinary(fd)

    def write(self, text: str) -> int:
        self.buffer.write(text.encode(self.encoding, self.errors))
        return len(text)

    def fileno(self) -> int:
        return self._fd

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


def route_child_output() -> None:
    """Makes subprocesses print into the console, as they would in a terminal REPL.

    On Windows, Popen without explicit stdout/stderr passes no handles to the child
    (bInheritHandles=False), and this detached process has no console for it to
    fall back on, so the child's output would vanish. Default both to fd 1/2.
    """
    if os.name != "nt":
        return
    import functools
    import inspect
    import subprocess

    original = subprocess.Popen.__init__
    signature = inspect.signature(original)

    @functools.wraps(original)
    def __init__(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        for stream, fd in (("stdout", 1), ("stderr", 2)):
            if bound.arguments.get(stream) is None:
                bound.arguments[stream] = fd
        original(*bound.args, **bound.kwargs)

    subprocess.Popen.__init__ = __init__


class ConsoleReader:
    """Reads byte ranges of console.txt; shared by the run thread and request threads."""

    def __init__(self, path: Path):
        self._file = open(path, "rb")
        self._lock = threading.Lock()

    def size(self) -> int:
        return os.fstat(self._file.fileno()).st_size

    def read(self, start: int, end: int) -> bytes:
        with self._lock:
            self._file.seek(start)
            return self._file.read(end - start)
