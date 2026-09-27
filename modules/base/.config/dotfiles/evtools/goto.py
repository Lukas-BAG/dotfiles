"""Find entries inside Everything dirs by id or by text (what ge/gel jump to).

An id "1" with year "25" means the entry "250001[-suffix]"; text is matched
case-insensitively against sidecar file names "<entry>_<description>...md"
(see entries.py). Everything here is read-only.
"""

import datetime
import fnmatch
import os
import re


def year_prefix(year_arg: str | None) -> str:
    """Two-digit year from "25", "2025" or None (this year); ValueError otherwise."""
    if year_arg is None:
        return datetime.date.today().strftime("%y")
    if re.fullmatch(r"[0-9]{4}", year_arg):
        return year_arg[2:]
    if re.fullmatch(r"[0-9]{2}", year_arg):
        return year_arg
    raise ValueError(year_arg)


def id_prefix(entry_id: str, year_arg: str | None) -> str:
    """The "<yy><seq>" to look for, e.g. ("1", "25") -> "250001"."""
    return f"{year_prefix(year_arg)}{int(entry_id):04d}"


def _names(base: str) -> list[str]:
    """Non-hidden names directly inside `base`, sorted, like a bash glob. [] if unreadable."""
    try:
        return sorted(n for n in os.listdir(base) if not n.startswith("."))
    except OSError:
        return []


def find_by_id(base: str, prefix: str) -> list[str]:
    """Entry dirs "<prefix>" or "<prefix>-<suffix>" directly inside `base`."""
    pattern = re.compile(rf"{re.escape(prefix)}(-[A-Za-z0-9]+)?")
    return [os.path.join(base, n) for n in _names(base)
            if pattern.fullmatch(n) and os.path.isdir(os.path.join(base, n))]


def find_by_text(base: str, needle: str) -> list[str]:
    """Entry dirs directly inside `base` with a "*_*.md" sidecar whose name contains `needle`.

    Case-insensitive; the entry is the sidecar name up to its first "_" and
    must exist as a dir. Each entry is listed once, however many sidecars match.
    """
    needle = needle.casefold()
    found = []
    for name in _names(base):
        if not fnmatch.fnmatchcase(name, "*_*.md") or needle not in name.casefold():
            continue
        entry = os.path.join(base, name.split("_", 1)[0])
        if os.path.isdir(entry) and entry not in found:
            found.append(entry)
    return found


def sidecar_name(entry: str) -> str:
    """Name of the entry's first "<entry>_*.md" sidecar minus ".md", or the entry's own name."""
    base, name = os.path.split(entry)
    for n in _names(base or "."):
        if n.startswith(name + "_") and n.endswith(".md") and \
                os.path.isfile(os.path.join(base, n)):
            return n[:-len(".md")]
    return name

