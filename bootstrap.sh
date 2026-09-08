#!/usr/bin/env bash
# One-shot remote bootstrap for dotfilesv3. Meant to be run on a fresh
# machine without cloning anything first, e.g.:
#
#   curl -fsSL https://raw.githubusercontent.com/Beinmann/dotfilesv3/main/bootstrap.sh | bash
#
# It installs git if needed, clones dotfilesv3 into ~/Main/dotfilesv3, and
# hands off to interactive_setup.py. It refuses to run if the current
# directory is already inside a git repo, to avoid cloning into the wrong
# place.
set -euo pipefail

REPO_URL="https://github.com/Beinmann/dotfilesv3"
TARGET_DIR="$HOME/Main/dotfilesv3"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Refusing to run: $(pwd) is already inside a git repo ($(git rev-parse --show-toplevel))." >&2
  echo "Run this from outside any git repo." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git not found, installing..."
  sudo apt update
  sudo apt -y install git
fi

if [ -e "$TARGET_DIR" ]; then
  echo "Refusing to run: $TARGET_DIR already exists." >&2
  exit 1
fi

echo "Cloning dotfilesv3 into $TARGET_DIR..."
mkdir -p "$(dirname "$TARGET_DIR")"
git clone "$REPO_URL" "$TARGET_DIR"

cd "$TARGET_DIR"

echo
echo "Handing off to interactive_setup.py..."
exec python3 interactive_setup.py </dev/tty
