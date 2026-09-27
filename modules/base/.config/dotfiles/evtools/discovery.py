"""Find every Everything dir on the system.

An Everything dir is any dir whose name contains "everything" (any case),
matching the Everything-like-dir convention, whether or not it has entries.
"""

import os
import re

# pruned wherever they appear: neither listed nor descended into
SKIP_NAMES = {".git", "node_modules", ".cache"}


def skip_paths(home: str) -> set[str]:
    # ~/.claude/projects holds one "-home-...-Everything-..." dir per Claude session
    return {
        os.path.join(home, ".claude", "projects"),
        os.path.join(home, ".local", "share", "Trash"),
    }


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
        if name in SKIP_NAMES or path in skip:
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
                    if child.name not in SKIP_NAMES and child.path not in skip \
                            and is_everything_name(child.name):
                        found.append(child.path)
                    continue
            except OSError:
                continue
            stack.append(child.path)
    return sorted(found)


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
