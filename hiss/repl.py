"""Running a snippet the way the Python REPL (PyREPL, 3.13+) runs a pasted block.

The whole snippet is parsed at once; every statement runs in order, only the last
one echoes its value, and the first statement that raises stops the rest.
Mirrors _pyrepl.console.InteractiveColoredConsole.runsource, copied rather than
imported so interpreters older than 3.13 behave the same.
"""
from __future__ import annotations

import ast
import linecache
import sys
import traceback


def run_source(source: str, namespace: dict, filename: str) -> str:
    """Returns "ok", "error" or "interrupted"."""
    linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
    try:
        # dont_inherit: this module's `from __future__` must not leak into user code
        tree = compile(source, filename, "exec", ast.PyCF_ONLY_AST, dont_inherit=True)
    except (SyntaxError, OverflowError, ValueError) as e:
        _show_syntax_error(e)
        return "error"
    for i, stmt in enumerate(tree.body):
        last = i == len(tree.body) - 1
        node = ast.Interactive([stmt]) if last else ast.Module([stmt], type_ignores=[])
        try:
            code = compile(node, filename, "single" if last else "exec", dont_inherit=True)
        except (SyntaxError, OverflowError, ValueError) as e:
            _show_syntax_error(e)
            return "error"
        try:
            exec(code, namespace)
        except KeyboardInterrupt as e:
            show_traceback(e)
            return "interrupted"
        except BaseException as e:  # SystemExit too: the session outlives a stray exit()
            show_traceback(e)
            return "error"
    return "ok"


def show_traceback(exc: BaseException) -> None:
    # Kept for post-mortem debugging, as the REPL does: `import pdb; pdb.pm()`
    sys.last_type, sys.last_value, sys.last_traceback = type(exc), exc, exc.__traceback__
    sys.last_exc = exc
    tb = exc.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename == __file__:
        tb = tb.tb_next
    sys.stderr.write("".join(traceback.format_exception(type(exc), exc, tb)))


def _show_syntax_error(exc: BaseException) -> None:
    sys.last_type, sys.last_value, sys.last_traceback = type(exc), exc, None
    sys.last_exc = exc
    sys.stderr.write("".join(traceback.format_exception_only(type(exc), exc)))
