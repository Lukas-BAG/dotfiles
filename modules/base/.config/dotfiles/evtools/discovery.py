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
SKIP_NAMES = {".git", "node_modules", ".cache", "site-packages", "dist-packages"}

# system and package dirs: nothing of the user's lives there
SYSTEM_DIRS = {"/proc", "/sys", "/dev", "/run", "/usr", "/lib", "/lib32", "/lib64", "/libx32",
               "/bin", "/sbin", "/boot", "/etc", "/opt", "/var/lib", "/snap"}

# kernel/pseudo filesystems, pruned wherever they are mounted
PSEUDO_FSTYPES = {"proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "securityfs",
                  "debugfs", "tracefs", "pstore", "bpf", "mqueue", "hugetlbfs", "configfs",
                  "fusectl", "binfmt_misc", "autofs", "efivarfs", "rpc_pipefs", "nsfs",
                  "selinuxfs"}

# network filesystems: slow to walk and maybe offline, so only searched with network=True
NETWORK_FSTYPES = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "ncpfs", "afs", "ceph", "glusterfs",
                   "lustre", "davfs", "sshfs", "fuse.sshfs", "fuse.rclone", "fuse.s3fs",
                   "fuse.gcsfuse", "fuse.glusterfs", "fuse.ceph-fuse", "fuse.davfs2",
                   "fuse.curlftpfs", "fuse.smbnetfs"}

MOUNTS_FILE = "/proc/self/mounts"


def skip_paths(home: str) -> set[str]:
    home = os.path.realpath(home)
    # all of ~/.claude (projects, todos, shell snapshots, ...) and ~/.local/share
    # (Trash, rootless Docker/containers storage, app data)
    return SYSTEM_DIRS | {os.path.join(home, ".claude"), os.path.join(home, ".local", "share")}


def _unescape_mount(field: str) -> str:
    # /proc/self/mounts writes space, tab, newline and backslash as \ooo
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), field)


def pruned_mounts(network: bool = False, mounts_file: str = MOUNTS_FILE) -> set[str]:
    """Mount points of pseudo filesystems, plus network ones unless `network`.

    Read from /proc/self/mounts; empty if that can't be read (not Linux).
    """
    skip = PSEUDO_FSTYPES if network else PSEUDO_FSTYPES | NETWORK_FSTYPES
    try:
        with open(mounts_file) as f:
            lines = f.read().splitlines()
    except OSError:
        return set()
    found = set()
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and fields[2] in skip:
            found.add(_unescape_mount(fields[1]))
    return found


def _pruned(path: str, skip: set[str]) -> bool:
    name = os.path.basename(path)
    parent = os.path.dirname(path)
    # <any>/.claude/projects holds one "-home-...-Everything-..." dir per Claude
    # session, also in archived homes and the dotfiles ai module
    claude_projects = name == "projects" and os.path.basename(parent) == ".claude"
    # Claude Code's per-session scratch dirs, named after the project path
    claude_tmp = parent == "/tmp" and name.startswith("claude-")
    return name in SKIP_NAMES or path in skip or claude_projects or claude_tmp


def is_everything_name(name: str) -> bool:
    return "everything" in name.casefold()


def enclosing_everything_dir(path: str) -> str | None:
    """The nearest of `path` and its ancestors that is an Everything dir, or None."""
    path = os.path.abspath(path)
    while True:
        if is_everything_name(os.path.basename(path)):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def find_everything_dirs(root: str, home: str | None = None, network: bool = False,
                         mounts_file: str = MOUNTS_FILE) -> list[str]:
    """All Everything dirs under `root` (including `root` itself), sorted.

    Never follows symlinks. Crosses into other local filesystems, but not into
    pseudo filesystems, or network ones unless `network`. System dirs, tool
    scratch dirs and Python venvs are pruned too (see skip_paths/_pruned);
    `root` itself is always searched, even if it is one of them. Unreadable
    dirs are skipped silently. Read-only.
    """
    home = home if home is not None else os.path.expanduser("~")
    skip = skip_paths(home) | pruned_mounts(network, mounts_file)
    root = os.path.normpath(root)

    found = []
    stack = [root]
    while stack:
        path = stack.pop()
        if path != root and _pruned(path, skip):
            continue
        try:
            with os.scandir(path) as it:
                children = list(it)
        except OSError:
            children = []
        # a Python venv (marked by pyvenv.cfg): pruned like the dirs above
        if path != root and any(c.name == "pyvenv.cfg" for c in children):
            continue
        if is_everything_name(os.path.basename(path) or path):
            found.append(path)
        for child in children:
            try:
                if child.is_dir(follow_symlinks=False):
                    stack.append(child.path)
            except OSError:
                continue
    return sorted(found)


# Global switch for the cache below. Off for now: searching ~ is fast enough on
# current machines, and a stale cache hides new Everything dirs. With it off,
# nothing is read or written and --fresh is accepted but does nothing.
CACHE_ENABLED = False
# Everything dirs are rarely created, so a search result this old is still good
CACHE_TTL = 6 * 60 * 60
# part of the cache key: bump when discovery rules change, so old results aren't reused
CACHE_VERSION = 2


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
                                network: bool = False) -> tuple[list[str], float | None]:
    """find_everything_dirs() through a cache in ~/.cache/dotfiles/.

    Returns (dirs, age): age is the cache entry's age in seconds, or None if
    a new search ran (and was written back). There is one entry per set of
    search options; entries older than CACHE_TTL are ignored, as is the whole
    cache with `fresh`. With CACHE_ENABLED off this is a plain search. Cached dirs that no longer exist are left out. The
    cache file is the only thing written.
    """
    home = home if home is not None else os.path.expanduser("~")
    if not CACHE_ENABLED:
        return find_everything_dirs(root, home, network), None
    root = os.path.normpath(root)
    path = cache_file(home)
    key = json.dumps({"v": CACHE_VERSION, "root": root, "network": network}, sort_keys=True)
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

    dirs = find_everything_dirs(root, home, network)
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
