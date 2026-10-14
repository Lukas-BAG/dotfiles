#!/bin/sh

# Bashmarks
source ~/.local/bin/bashmarks.sh

q() {
  echo "$(p q)"
}

a() {
  echo "$(p a)"
}

y() {
  echo "$(p y)"
}

# Using ranger to select cwd
ranger_cd() {
    tempfile=$(mktemp)
    # Debian's ranger runs with `python -O` but ships no .opt-1.pyc files, so it
    # recompiles on every start (~0.5s). Redirect bytecode to a user cache so
    # the first run compiles once and later runs are fast.
    PYTHONPYCACHEPREFIX="${XDG_CACHE_HOME:-$HOME/.cache}/pycache" \
        ranger --choosedir="$tempfile" "${@:-$(pwd)}" --cmd="set show_hidden true"
    if chosen_dir=$(cat "$tempfile"); then
        cd "$chosen_dir"
    fi
    rm -f "$tempfile"
}
