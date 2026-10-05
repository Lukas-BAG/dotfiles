"""Describe an entry dir for humans: what is in it and what would be lost.

`entry_info(path)` is read-only and reusable (remove.py shows it before
deleting; `everything show`/`check` could too). It never follows symlinks:
a symlink is counted as one, whatever it points to.
"""

import os
import subprocess
from dataclasses import dataclass, field

from . import entries

GIT_TIMEOUT = 15  # seconds per git call


@dataclass(frozen=True)
class EntryInfo:
    path: str
    entry: entries.EntryName
    sidecars: tuple[entries.SidecarName, ...]  # those next to the dir; exactly one is normal
    size: int                 # apparent bytes, like `du -sb`
    files: int                # regular files and other non-dir, non-symlink entries
    dirs: int                 # sub dirs, not counting the entry dir itself
    symlinks: int
    newest: float | None      # mtime of the newest file inside (the dir's own if it has none)
    git_repos: tuple[str, ...] = ()  # repo roots, relative to the entry ("." = the entry)
    warnings: tuple[str, ...] = field(default=())
    mount_points: tuple[str, ...] = ()  # sub dirs on another filesystem, relative paths


def git_warnings(repo: str, label: str) -> list[str]:
    """Why deleting the repo at `repo` would lose work; [] if it is clean and pushed."""
    def git(*args: str) -> str | None:
        try:
            res = subprocess.run(["git", "--no-optional-locks", "-C", repo, *args],
                                 capture_output=True, text=True, timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return res.stdout if res.returncode == 0 else None

    status = git("status", "--porcelain")
    unpushed = git("log", "--branches", "--not", "--remotes", "--oneline")
    if status is None or unpushed is None:
        return [f"git repo '{label}': state could not be read (git missing or repo broken), "
                "assume it has unsaved work"]
    found = []
    if status.strip():
        found.append(f"git repo '{label}': {len(status.splitlines())} uncommitted change(s)")
    if unpushed.strip():
        found.append(f"git repo '{label}': {len(unpushed.splitlines())} commit(s) not on any remote")
    return found


def entry_info(path: str) -> EntryInfo:
    """Facts about the entry dir `path` (its name must be an entry id)."""
    path = os.path.abspath(path)
    parent, name = os.path.split(path)
    entry = entries.parse_entry(name)
    if entry is None:
        raise ValueError(f"not an entry dir name: {name}")
    sidecars = tuple(s for s in entries.list_sidecars(parent) if s.entry.name == name)

    files = dirs = links = 0
    newest = None
    repos, mounts = [], []
    root_dev = os.lstat(path).st_dev
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        rel = os.path.relpath(dirpath, path)
        if ".git" in dirnames or ".git" in filenames:
            repos.append(rel)
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                links += 1
                dirnames.remove(d)  # os.walk lists dir symlinks here, but never enters them
                continue
            dirs += 1
            try:
                if os.lstat(full).st_dev != root_dev:
                    mounts.append(os.path.relpath(full, path))
            except OSError:
                pass
        for f in filenames:
            full = os.path.join(dirpath, f)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if os.path.islink(full):
                links += 1
                continue
            files += 1
            newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
    if newest is None:
        newest = os.lstat(path).st_mtime

    warnings = []
    for rel in repos:
        warnings += git_warnings(os.path.join(path, rel), rel)
    if links:
        warnings.append(f"{links} symlink(s) inside; they are not followed, so what they "
                        "point to is not part of the entry")
    for m in mounts:
        warnings.append(f"'{m}' is another filesystem (mount point)")
    return EntryInfo(path, entry, sidecars, entries.tree_size(path), files, dirs, links,
                     newest, tuple(repos), tuple(warnings), tuple(mounts))
