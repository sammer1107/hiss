#!/usr/bin/env bash
# Installs the `hiss` command: a launcher in ~/bin pointing at this checkout.
# Rerun it after moving the checkout.
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/bin"
sed "s|@ROOT@|$root|" "$root/hiss.sh" > "$HOME/bin/hiss"
chmod +x "$HOME/bin/hiss"
echo "installed $HOME/bin/hiss -> $root"
case ":$PATH:" in
    *":$HOME/bin:"*) ;;
    *) echo "~/bin is not on PATH; add it:  echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.bashrc" ;;
esac
