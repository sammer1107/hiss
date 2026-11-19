#!/usr/bin/env bash
# HISS launcher template; install.sh fills in @ROOT@ and copies it to ~/bin/hiss.
# Works in Git Bash on Windows and on Linux/macOS.
script="@ROOT@/run.py"
python=python3
if command -v cygpath >/dev/null; then  # Git Bash on Windows
    python=python
    # Git Bash rewrites path-like arguments ("/usr/x" -> "C:/Program Files/Git/usr/x") when
    # calling a Windows program, which would corrupt `hiss run NAME -c CODE`; switch that off.
    export MSYS_NO_PATHCONV=1
    script="$(cygpath -w "$script")"
fi
exec "$python" "$script" "$@"
