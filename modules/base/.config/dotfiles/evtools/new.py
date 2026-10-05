"""Create the next entry in an Everything dir (what mynew does).

The new entry is the dir "<yy><seq>[-suffix]" plus an empty sidecar
"<entry>_<description>[_@tag...].md" next to it (see entries.py). seq is one
past the highest id in the dir (whatever its suffix) and restarts at 0001
when the year changes. An empty dir gets seq 0001 (the CLI confirms first
if its name doesn't contain "everything"). The suffix is kept in the dir's
".mynew-suffix" file; if there is none, it comes from ~/.devbox_id, and
only if that is missing too is it asked for.

With rename.py and saved_locations.py the only parts of evtools that write;
new.py writes only those three things and never overwrites anything, except
that `--ask-suffix` replaces ".mynew-suffix".
"""

import datetime
import os
import re
from dataclasses import dataclass

from . import entries
from .check import SUFFIX_FILE

SUFFIX_RE = re.compile(r"[A-Za-z0-9]*")  # "" = no suffix
DEVBOX_FILE = ".devbox_id"  # in $HOME: the suffix this machine uses


class Refusal(Exception):
    """Why no entry was created; the message is shown as is."""


@dataclass(frozen=True)
class Plan:
    dir: str
    entry: str       # e.g. "260004-nry"
    sidecar: str     # e.g. "260004-nry_fire_drill_@ai.md"
    rolled_over: str | None  # the previous year prefix, if seq restarted at 0001


def current_year() -> str:
    return datetime.date.today().strftime("%y")


def snake(description: str) -> str:
    """"Fire Drill (v2)!" -> "fire_drill_v2", as mynew always did."""
    return re.sub(r"[^a-z0-9]+", "_", description.lower()).strip("_")


def read_suffix(everything_dir: str) -> str | None:
    """The saved suffix ("" = none), or None if none was saved yet."""
    try:
        with open(os.path.join(everything_dir, SUFFIX_FILE)) as f:
            suffix = f.read().strip()
    except FileNotFoundError:
        return None
    if not SUFFIX_RE.fullmatch(suffix):
        raise Refusal(f"{SUFFIX_FILE} holds '{suffix}', but a suffix may only be "
                      "letters and digits - fix or delete that file")
    return suffix


def read_devbox_suffix(home: str) -> str | None:
    """The suffix in ~/.devbox_id, or None if the file is missing or blank."""
    try:
        with open(os.path.join(home, DEVBOX_FILE)) as f:
            suffix = f.read().strip()
    except FileNotFoundError:
        return None
    if not suffix:
        return None
    if not SUFFIX_RE.fullmatch(suffix):
        raise Refusal(f"~/{DEVBOX_FILE} holds '{suffix}', but a suffix may only be "
                      "letters and digits - fix or delete that file")
    return suffix


def other_files(everything_dir: str) -> list[str]:
    """Everything in the dir except ".mynew-suffix"; an empty result means a new dir."""
    return [n for n in os.listdir(everything_dir) if n != SUFFIX_FILE]


def check_suffix(answer: str) -> str:
    suffix = answer.strip()
    if not SUFFIX_RE.fullmatch(suffix):
        raise Refusal(f"suffix '{suffix}' may only be letters and digits, refusing")
    return suffix


def plan(everything_dir: str, description: str, tags: list[str], suffix: str,
         year: str) -> Plan:
    """Work out the next entry without touching anything; Refusal if there's none."""
    found = entries.list_entries(everything_dir)
    latest = None
    if found:
        latest = max(found, key=lambda e: e.id)
        if latest.yy > year:
            raise Refusal(f"highest entry '{latest.name}' is from a later year than this one "
                          f"('{year}'), refusing")
        seq = int(latest.seq) + 1 if latest.yy == year else 1
        if seq > 9999:
            raise Refusal(f"no ids left for 20{year} after '{latest.name}', refusing")
    else:
        others = other_files(everything_dir)
        if others:
            name = os.path.basename(os.path.abspath(everything_dir))
            raise Refusal(f"'{name}' has no <yy><seq>[-suffix] entries but contains other files "
                          f"({len(others)} item{'s' if len(others) != 1 else ''}), so it isn't "
                          "treated as a new Everything dir; use an empty dir, or add a first "
                          "entry by hand")
        seq = 1  # a new, empty Everything dir

    words = snake(description)
    if not words:
        raise Refusal("description has no letters or digits, refusing")
    tags = [t[1:] if t.startswith("@") else t for t in tags]
    for t in tags:
        if not t or "/" in t:
            raise Refusal(f"invalid tag '@{t}', refusing")

    entry = f"{year}{seq:04d}" + (f"-{suffix}" if suffix else "")
    sidecar = f"{entry}_{words}" + "".join(f"_@{t}" for t in tags) + ".md"
    # also catches sidecars left over for this id, whatever their description
    taken = sorted(n for n in os.listdir(everything_dir)
                   if n == entry or n.startswith(entry + "_"))
    if taken:
        raise Refusal(f"'{taken[0]}' already exists, refusing to touch it")
    rolled = latest.yy if latest and latest.yy != year else None
    return Plan(everything_dir, entry, sidecar, rolled)


def create(p: Plan, save_suffix: str | None, replace_suffix: bool = False) -> str:
    """Write the entry dir and sidecar (and the suffix, on first use); return the dir.

    `replace_suffix` (--ask-suffix) overwrites an existing ".mynew-suffix".
    """
    if save_suffix is not None:
        with open(os.path.join(p.dir, SUFFIX_FILE), "w" if replace_suffix else "x") as f:
            f.write(save_suffix)
    path = os.path.join(p.dir, p.entry)
    os.mkdir(path)
    try:
        open(os.path.join(p.dir, p.sidecar), "x").close()
    except OSError:
        os.rmdir(path)  # still empty: we just made it
        raise
    return path
