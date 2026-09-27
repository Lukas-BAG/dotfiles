"""Create the next entry in an Everything dir (what mynew does).

The new entry is the dir "<yy><seq>[-suffix]" plus an empty sidecar
"<entry>_<description>[_@tag...].md" next to it (see entries.py). seq is one
past the highest id in the dir (whatever its suffix) and restarts at 0001
when the year changes. The suffix is asked for once per Everything dir and
kept in its ".mynew-suffix" file.

The only part of evtools that writes, and only those three things; it never
overwrites anything.
"""

import datetime
import os
import re
from dataclasses import dataclass

from . import entries
from .check import SUFFIX_FILE

SUFFIX_RE = re.compile(r"[A-Za-z0-9]*")  # "" = no suffix


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


def check_suffix(answer: str) -> str:
    suffix = answer.strip()
    if not SUFFIX_RE.fullmatch(suffix):
        raise Refusal(f"suffix '{suffix}' may only be letters and digits, refusing")
    return suffix


def plan(everything_dir: str, description: str, tags: list[str], suffix: str,
         year: str) -> Plan:
    """Work out the next entry without touching anything; Refusal if there's none."""
    found = entries.list_entries(everything_dir)
    if not found:
        raise Refusal(f"no existing <yy><seq>[-suffix] entries found in {everything_dir} - "
                      "this doesn't look like an Everything dir, refusing")
    latest = max(found, key=lambda e: e.id)
    if latest.yy > year:
        raise Refusal(f"highest entry '{latest.name}' is from a later year than this one "
                      f"('{year}'), refusing")
    seq = int(latest.seq) + 1 if latest.yy == year else 1
    if seq > 9999:
        raise Refusal(f"no ids left for 20{year} after '{latest.name}', refusing")

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
    return Plan(everything_dir, entry, sidecar, latest.yy if latest.yy != year else None)


def create(p: Plan, save_suffix: str | None) -> str:
    """Write the entry dir and sidecar (and the suffix, on first use); return the dir."""
    if save_suffix is not None:
        with open(os.path.join(p.dir, SUFFIX_FILE), "x") as f:
            f.write(save_suffix)
    path = os.path.join(p.dir, p.entry)
    os.mkdir(path)
    try:
        open(os.path.join(p.dir, p.sidecar), "x").close()
    except OSError:
        os.rmdir(path)  # still empty: we just made it
        raise
    return path
