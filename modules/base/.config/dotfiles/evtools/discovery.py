"""Find every Everything dir on the system.

An Everything dir is any dir whose name contains "everything" (any case),
matching the Everything-like-dir convention, whether or not it has entries.
"""

import json
import os
import re
import tempfile
import time

# pruned wherever they appear: neither listed nor descended into
SKIP_NAMES = {".git", "node_modules", ".cache"}


def skip_paths(home: str) -> set[str]:
    return {os.path.join(home, ".local", "share", "Trash")}


def _pruned(path: str, skip: set[str]) -> bool:
    name = os.path.basename(path)
    # <any>/.claude/projects holds one "-home-...-Everything-..." dir per Claude
    # session, also in archived homes and the dotfiles ai module
    claude_projects = name == "projects" and os.path.basename(os.path.dirname(path)) == ".claude"
    return name in SKIP_NAMES or path in skip or claude_projects


def is_everything_name(name: str) -> bool:
    return "everything" in name.casefold()


def find_everything_dirs(root: str, home: str | None = None) -> list[str]:
    """All Everything dirs under `root` (including `root` itself), sorted.

    Never follows symlinks and stays on root's filesystem (a mount point is
    still listed if it matches, just not descended into). Unreadable dirs
    are skipped silently. Read-only.
    """
    home = home if home is not None else os.path.expanduser("~")
    skip = skip_paths(home)
    root = os.path.normpath(root)
    root_dev = os.lstat(root).st_dev

    found = []
    stack = [root]
    while stack:
        path = stack.pop()
        name = os.path.basename(path) or path
        if _pruned(path, skip):
            continue
        if is_everything_name(name):
            found.append(path)
        try:
            with os.scandir(path) as it:
                children = list(it)
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_dir(follow_symlinks=False):
                    continue
                if child.stat(follow_symlinks=False).st_dev != root_dev:
                    if not _pruned(child.path, skip) and is_everything_name(child.name):
                        found.append(child.path)
                    continue
            except OSError:
                continue
            stack.append(child.path)
    return sorted(found)


# Everything dirs are rarely created, so a search result this old is still good
CACHE_TTL = 6 * 60 * 60
# part of the cache key: bump when discovery rules change, so old results aren't reused
CACHE_VERSION = 1


def cache_file(home: str | None = None) -> str:
    home = home if home is not None else os.path.expanduser("~")
    return os.path.join(home, ".cache", "dotfiles", "everything-dirs.json")


def _read_cache(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(path: str, data: dict) -> None:
    """Atomically replace the cache file (temp file + rename). Failures are ignored."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".everything-dirs.")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
    except OSError:
        pass


def find_everything_dirs_cached(root: str, home: str | None = None, fresh: bool = False,
                                ) -> tuple[list[str], float | None]:
    """find_everything_dirs() through a cache in ~/.cache/dotfiles/.

    Returns (dirs, age): age is the cache entry's age in seconds, or None if
    a new search ran (and was written back). There is one entry per set of
    search options; entries older than CACHE_TTL are ignored, as is the whole
    cache with `fresh`. Cached dirs that no longer exist are left out. The
    cache file is the only thing written.
    """
    home = home if home is not None else os.path.expanduser("~")
    root = os.path.normpath(root)
    path = cache_file(home)
    key = json.dumps({"v": CACHE_VERSION, "root": root}, sort_keys=True)
    data = _read_cache(path)
    now = time.time()

    entry = data.get(key)
    if not fresh and isinstance(entry, dict):
        try:
            age = now - float(entry["time"])
            dirs = [d for d in entry["dirs"] if isinstance(d, str)]
        except (KeyError, TypeError, ValueError):
            age = None
        if age is not None and 0 <= age < CACHE_TTL:
            return [d for d in dirs if os.path.isdir(d)], age

    dirs = find_everything_dirs(root, home)
    data[key] = {"time": now, "dirs": dirs}
    _write_cache(path, data)
    return dirs, None


def bookmark_dir(sdirs: str | None = None) -> str | None:
    """Resolved path of the bashmarks "e" bookmark (what ge jumps to), if set."""
    sdirs = sdirs or os.environ.get("SDIRS") or os.path.expanduser("~/.sdirs")
    try:
        with open(sdirs) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    target = None
    for line in lines:  # sourced top to bottom, so the last definition wins
        m = re.match(r'^\s*export\s+DIR_e=(?:"(.*)"|\'(.*)\'|(\S*))\s*$', line)
        if m:
            target = next(g for g in m.groups() if g is not None)
    if not target:
        return None
    target = os.path.expanduser(os.path.expandvars(target))
    return os.path.realpath(target) if os.path.isdir(target) else None
