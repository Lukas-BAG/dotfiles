"""Find Everything dirs on the system (only `everything scan` does this).

An Everything dir is any dir whose name contains "everything" (any case),
matching the Everything-like-dir convention, whether or not it has entries,
or a dir the user marked (see saved_locations.py).
"""

import os
import re

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


def enclosing_everything_dir(path: str, marked: list[str] | None = None) -> str | None:
    """The nearest of `path` and its ancestors that is an Everything dir, or None.

    A dir counts if its name contains "everything" or if it is one of the
    `marked` dirs (saved_locations.load(); compared by real path).
    """
    path = os.path.abspath(path)
    marked_real = {os.path.realpath(m) for m in marked or []}
    while True:
        if is_everything_name(os.path.basename(path)) or os.path.realpath(path) in marked_real:
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def find_everything_dirs(root: str, home: str | None = None, network: bool = False,
                         mounts_file: str = MOUNTS_FILE, links: bool = False) -> list[str]:
    """All Everything dirs under `root` (including `root` itself), sorted.

    Never follows symlinks. With `links`, symlinks whose own name matches and
    whose target is a dir are included too (as the link path, see is_link);
    their target is checked, never walked, so link loops can't hang the search.
    Broken links are left out. Crosses into other local filesystems, but not into
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
                elif links and child.is_symlink() and is_everything_name(child.name) \
                        and os.path.isdir(child.path):
                    found.append(child.path)
            except OSError:
                continue
    return sorted(found)


def is_link(path: str) -> bool:
    """Whether a path from find_everything_dirs(links=True) is a symlinked Everything dir."""
    return os.path.islink(path)


def drop_duplicate_links(dirs: list[str]) -> list[str]:
    """`dirs` without symlinked Everything dirs whose target is already in it.

    A link is kept only if it is the sole way to reach its target in `dirs`
    (e.g. the target is outside the searched root or on an unsearched
    filesystem); of several links to one such target, the first is kept. For
    commands that search or count every dir once. Order is preserved.
    """
    seen = {os.path.realpath(d) for d in dirs if not is_link(d)}
    kept = []
    for d in dirs:
        if is_link(d):
            target = os.path.realpath(d)
            if target in seen:
                continue
            seen.add(target)
        kept.append(d)
    return kept


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
