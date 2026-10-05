"""The saved list of Everything dirs: what `--all`, `list` and friends search.

One absolute path per line in ~/.config/dotfiles/system_local/everything-dirs
(`#` comments and blank lines are ignored). The file is real and machine-local:
it only exists once `everything scan` or `everything mark` created it, so a
missing file means "not set up yet" (NotSetUp), unlike an empty one.
Paths are stored as given (a symlinked dir keeps its link path); two paths to
the same real dir count as one.
"""

import os
import tempfile
from dataclasses import dataclass

from . import discovery

FILE_PARTS = (".config", "dotfiles", "system_local", "everything-dirs")
HINT = "run `everything scan` or `everything mark` to set up the list of Everything dirs"


class NotSetUp(Exception):
    """The list file doesn't exist yet."""


@dataclass(frozen=True)
class AllDirs:
    dirs: list[str]     # existing dirs, each real dir once, sorted
    missing: list[str]  # saved dirs that no longer exist


def list_file(home: str | None = None) -> str:
    home = home if home is not None else os.path.expanduser("~")
    return os.path.join(home, *FILE_PARTS)


def load(home: str | None = None) -> list[str] | None:
    """The saved paths in file order, or None if the file doesn't exist."""
    try:
        with open(list_file(home)) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return None
    paths = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            paths.append(line)
    return paths


def _write(paths: list[str], home: str | None, header: list[str] | None = None) -> None:
    """Atomically replace the list file (temp file + rename), keeping its comments."""
    path = list_file(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    comments = header if header is not None else _comments(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".everything-dirs.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("".join(c + "\n" for c in comments))
            f.write("".join(p + "\n" for p in paths))
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _comments(path: str) -> list[str]:
    try:
        with open(path) as f:
            return [l.rstrip("\n") for l in f if l.startswith("#")]
    except OSError:
        return []


def create(home: str | None = None) -> bool:
    """Create an empty list file if there is none; whether it was created."""
    if load(home) is not None:
        return False
    _write([], home, ["# Everything dirs searched by --all and list; managed by "
                      "`everything scan/mark/unmark`, one absolute path per line."])
    return True


def contains(path: str, home: str | None = None) -> bool:
    """Whether `path` (or another path to the same real dir) is saved."""
    real = os.path.realpath(path)
    return any(os.path.realpath(p) == real for p in load(home) or [])


def add(path: str, home: str | None = None) -> bool:
    """Save `path`, creating the file if needed; False if it was already saved."""
    saved = load(home) or []
    if contains(path, home):
        create(home)
        return False
    _write(saved + [path], home)
    return True


def remove(path: str, home: str | None = None) -> bool:
    """Drop the saved path equal to `path` (exact text); False if it isn't saved."""
    saved = load(home)
    if saved is None or path not in saved:
        return False
    _write([p for p in saved if p != path], home)
    return True


def all_dirs(home: str | None = None, sdirs: str | None = None) -> AllDirs:
    """Saved dirs plus the "e" bashmark dir, each real dir once; NotSetUp if no file.

    Saved paths come first when two paths reach the same dir, so a saved
    symlink keeps its link name. The result is sorted by path.
    """
    saved = load(home)
    if saved is None:
        raise NotSetUp(HINT)
    found, missing, seen = [], [], set()
    bookmark = discovery.bookmark_dir(sdirs)
    for p in saved + ([bookmark] if bookmark else []):
        if not os.path.isdir(p):
            if p not in missing:
                missing.append(p)
            continue
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            found.append(p)
    return AllDirs(sorted(found), missing)
