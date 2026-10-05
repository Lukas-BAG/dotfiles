"""Delete an entry dir together with its sidecar (what `everything remove` does).

Checks come first and delete nothing: the cwd must be in an entry dir (the
nearest dir named like an entry - never an outer one), with exactly one sidecar
next to it; the entry dir may not be a symlink or mount point, nor contain a
mount point. The caller shows info.entry_info() and asks for a typed
confirmation; `delete` then moves both to the trash (gio trash / trash-put, in
one call so a restore brings both back) or, with no trash tool, deletes them
for good.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import entries, info
from .rename import Refusal, Target


@dataclass(frozen=True)
class Method:
    trash_cmd: list[str] | None  # None = permanent delete

    @property
    def trash(self) -> bool:
        return self.trash_cmd is not None

    @property
    def banner(self) -> str:
        return ("Will be MOVED TO THE TRASH (restorable)" if self.trash
                else "Will be PERMANENTLY DELETED (no trash tool found)")


class NotInEntry(Refusal):
    """The cwd isn't inside any dir named like an entry."""


def find_entry(cwd: str, for_delete: bool = True) -> Target:
    """The entry dir at or above `cwd` and its one sidecar; Refusal if unsafe or unclear.

    `for_delete=False` (read-only callers like info) skips the symlink and
    mount point refusals.

    Unlike rename, it never skips an entry dir that has no usable sidecar to
    reach an outer one: deleting a different entry than the one you are in
    would be far worse than refusing.
    """
    path = cwd
    while True:
        parent, name = os.path.split(path)
        if not name:
            raise NotInEntry("not inside an entry dir - nothing deleted")
        if entries.parse_entry(name):
            break
        path = parent
    found = [s for s in entries.list_sidecars(parent) if s.entry.name == name]
    if not found:
        raise Refusal(f"'{name}' has no sidecar file next to it, so it isn't treated as an "
                      "entry of an Everything dir - nothing deleted")
    if len(found) > 1:
        raise Refusal(f"'{name}' has {len(found)} sidecar files ("
                      + ", ".join(s.name for s in found) + ") - fix that first, nothing deleted")
    if for_delete and os.path.islink(path):
        raise Refusal(f"'{name}' is a symlink, refusing to delete through it")
    if for_delete and os.path.ismount(path):
        raise Refusal(f"'{name}' is a mount point, refusing")
    return Target(path, os.path.join(parent, found[0].name), found[0])


def check_deletable(target: Target) -> info.EntryInfo:
    """entry_info for the target, after refusing anything that would cross a mount point."""
    ei = info.entry_info(target.entry_dir)
    if ei.mount_points:
        raise Refusal(f"'{ei.mount_points[0]}' inside the entry is another filesystem "
                      "(mount point), refusing - nothing deleted")
    return ei


def confirm_word(description: str, seq: str) -> str:
    """The word the user must type to confirm: the first word of the description.

    Words are split at "_" (the sidecar's snake_case) and whitespace. A first
    word is only usable with at least 3 letters or digits; otherwise the first 6
    characters of the description are used (all of it if shorter). A description
    with nothing to type at all (empty, or only blanks) falls back to the entry's
    sequence number, so there is always a word that has to be read and typed.
    """
    description = description.strip()
    words = re.split(r"[_\s]+", description, maxsplit=1)
    first = words[0]
    if sum(c.isalnum() for c in first) >= 3:
        return first
    return description[:6].strip() or seq


def choose_method() -> Method:
    """Trash if gio or trash-put is installed, else permanent deletion."""
    if shutil.which("gio"):
        return Method(["gio", "trash", "--"])
    if shutil.which("trash-put"):
        return Method(["trash-put", "--"])
    return Method(None)


def delete(target: Target, method: Method) -> list[str]:
    """Remove the entry dir and its sidecar; returns what was removed.

    Raises Refusal naming exactly what is gone and what is left if a step fails,
    so a sidecar is never orphaned silently.
    """
    for p in (target.entry_dir, target.sidecar_path):
        if not os.path.lexists(p):
            raise Refusal(f"'{os.path.basename(p)}' is already gone, nothing deleted")
    names = [os.path.basename(target.entry_dir) + "/", os.path.basename(target.sidecar_path)]
    if method.trash:
        res = subprocess.run([*method.trash_cmd, target.entry_dir, target.sidecar_path],
                             capture_output=True, text=True)
        left = [n for n, p in zip(names, (target.entry_dir, target.sidecar_path))
                if os.path.lexists(p)]
        if res.returncode != 0 or left:
            gone = [n for n in names if n not in left]
            raise Refusal(f"{method.trash_cmd[0]} failed ({res.stderr.strip() or 'exit ' + str(res.returncode)}); "
                          f"removed: {', '.join(gone) or 'nothing'}; still there: "
                          f"{', '.join(left) or 'nothing'}")
        return names
    done = []
    try:
        shutil.rmtree(target.entry_dir)
        done.append(names[0])
        os.unlink(target.sidecar_path)
        done.append(names[1])
    except OSError as e:
        left = [n for n in names if n not in done]
        part = " (possibly partly deleted)" if not done else ""
        raise Refusal(f"{e.strerror or e}: removed: {', '.join(done) or 'nothing'}; still "
                      f"there: {', '.join(left)}{part}")
    return done
