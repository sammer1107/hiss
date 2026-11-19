# HISS: Headless Interactive Session Server

Persistent Python REPL sessions, driven one shell command at a time, each identified by a name. Built for agents (Claude Code) that can't sit at a `>>>` prompt but want one: explore an unfamiliar API with a live client object, keep loaded data in memory, avoid re-running calls that have side effects.

## Install

Needs Python 3.8+ and bash (Git Bash on Windows); no packages.

```bash
git clone <this repo> ~/.claude/tools/hiss    # any location works
~/.claude/tools/hiss/install.sh               # writes ~/bin/hiss pointing at the checkout
python ~/.claude/tools/hiss/tests/test_hiss.py
```

## Usage

```bash
hiss start api                       # prints its handle: "started session api-k3f9"
hiss run api-k3f9 <<'EOF'
import requests
s = requests.Session()
r = s.get("https://example.com")
r.status_code
EOF
hiss run api-k3f9 -c 'r.headers["content-type"]'
hiss wait api-k3f9                   # more output from a snippet still running
hiss interrupt api-k3f9              # Ctrl+C; variables survive
hiss stop api-k3f9
hiss list
hiss log api-k3f9                    # transcript
```

- Every `start` creates a new session under a unique handle, NAME-xxxx, and every other command takes that handle. Several agents can use the same NAME without sharing, stopping or overwriting each other's sessions. Meant for exploratory work, not long-lived services.

- A snippet runs like a block pasted into the Python 3.13+ REPL: it is parsed whole, only the last statement echoes its value, and the first statement that raises stops the rest.
- Output is what the REPL would print: prints, values, tracebacks, warnings, child processes, in order. A status line goes to stderr: `[hiss api #3 ok 0.41s]`.
- Exit codes: 0 ok, 1 the snippet raised or was interrupted, 2 still running, 3 hiss itself failed (no such session, busy, ...).
- `run` and `wait` block up to `--timeout` seconds (default 60), then return what has printed so far and exit 2; the snippet keeps running. A session runs one snippet at a time and refuses another while busy.
- Output over `--max` characters (default 20000, 0 = no limit) is cut in the middle; the marker names the file holding the full output.
- A session exits after `--idle` minutes without requests (default 10). A dead session is never restarted silently: `run` fails and points at the transcript.
- Prefer heredocs to `-c` for anything with quotes.

## Files

- `run.py`: entry point for the CLI and the session process.
- `hiss/cli.py`: commands. `hiss/client.py`: request protocol (one JSON line each way over localhost TCP, with a per-session token).
- `hiss/server.py`: the session process. User code runs on its main thread, so Ctrl+C works; requests are served on other threads.
- `hiss/repl.py`: REPL semantics, copied from `_pyrepl.console.InteractiveColoredConsole.runsource` so older interpreters behave the same.
- `hiss/console.py`: output capture. The session's fd 1 and 2 are `console.txt`; a run's output is the byte range it added.
- `hiss/transcript.py`: the append-only log of every snippet and its full output.
- `hiss/cleanup.py`: runs on every `start`. It deletes a dead session's `console.txt` and `out/`, which only duplicate its transcript, and the whole folder once the transcript is 7 days old.
- `sessions/HANDLE/`: `state.json` (exists while alive), `console.txt`, `transcript.log`, `out/N.txt`. `HISS_HOME` moves this.
- `hiss.sh`: the launcher template; `install.sh` fills in the checkout's path and writes it to `~/bin/hiss`. It uses `python` in Git Bash, where it also turns off MSYS path conversion that would rewrite `-c '"/usr/x"'`, and `python3` elsewhere.

## Platforms

Developed and tested on Windows (Git Bash). On Linux and macOS the session detaches with `start_new_session`, child processes inherit fd 1/2 natively, and SIGINT is reset to Python's handler in case it was inherited as ignored. It hasn't been run there yet: run the tests first.

## Gotchas

- Windows: a subprocess started without explicit stdout/stderr gets no handles, so its output would vanish. The session patches `subprocess.Popen` to default them to fd 1/2.
- Ctrl+C can't break code blocked inside C, such as a socket `accept()`. `interrupt` says so after 5 s; `stop` kills the session.
- `exit()` in a snippet doesn't end the session; it's reported as an error.
- `stop` kills by pid only when the session's port still accepts connections, which proves the process is alive. When nothing listens, the process is gone and its pid may belong to an unrelated process by now.

## Tests

```bash
python tests/test_hiss.py
```
