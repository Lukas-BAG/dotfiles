"""Parse Everything-dir entry names.

An entry is a dir named "<yy><seq>[-suffix]" (e.g. "260003-nry"), usually
paired with a sidecar file "<entry>_<description>[_@tag...].<ext>" next to
it (e.g. "260003-nry_working_on_dotfiles_@ai.md"). See mynew() in
functions.d/base.sh, which creates both.
"""

import os
import re
from dataclasses import dataclass

ENTRY_RE = re.compile(r"^(?P<yy>[0-9]{2})(?P<seq>[0-9]{4})(?:-(?P<suffix>[A-Za-z0-9]+))?$")
SIDECAR_RE = re.compile(r"^(?P<entry>[0-9]{6}(?:-[A-Za-z0-9]+)?)_(?P<rest>.+)$")


@dataclass(frozen=True)
class EntryName:
    name: str
    yy: str
    seq: str
    suffix: str | None

    @property
    def id(self) -> int:
        """The 6-digit "<yy><seq>" as a number, e.g. 260003."""
        return int(self.yy + self.seq)


@dataclass(frozen=True)
class SidecarName:
    name: str
    entry: EntryName
    description: str
    tags: tuple[str, ...]
    ext: str  # including the dot, "" if none


def parse_entry(name: str) -> EntryName | None:
    """Parse an entry dir name, or return None if it isn't one."""
    m = ENTRY_RE.match(name)
    if not m:
        return None
    return EntryName(name, m["yy"], m["seq"], m["suffix"])


def parse_sidecar(name: str) -> SidecarName | None:
    """Parse a sidecar file name (any extension), or return None if it isn't one."""
    stem, ext = os.path.splitext(name)
    m = SIDECAR_RE.match(stem)
    if not m:
        return None
    entry = parse_entry(m["entry"])
    description, *tags = m["rest"].split("_@")
    return SidecarName(name, entry, description, tuple(tags), ext)


def list_entries(everything_dir: str) -> list[EntryName]:
    """Entry dirs directly inside `everything_dir`, sorted by name.

    Like a bash "$dir"/*/ glob: symlinks to dirs count, hidden names don't
    (no entry name starts with "." anyway). Unreadable dirs give [].
    """
    try:
        with os.scandir(everything_dir) as it:
            names = [e.name for e in it if e.is_dir()]  # is_dir() follows symlinks
    except OSError:
        return []
    return [entry for entry in map(parse_entry, sorted(names)) if entry]


def list_sidecars(everything_dir: str) -> list[SidecarName]:
    """Sidecar files directly inside `everything_dir`, sorted by name.

    Symlinks to files count; dirs (incl. "temp") and names that don't parse
    (e.g. ".mynew-suffix") don't. Unreadable dirs give [].
    """
    try:
        with os.scandir(everything_dir) as it:
            names = [e.name for e in it if e.is_file()]  # is_file() follows symlinks
    except OSError:
        return []
    return [s for s in map(parse_sidecar, sorted(names)) if s]
