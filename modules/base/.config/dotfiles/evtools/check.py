"""Check that Everything dirs follow the entry/sidecar naming convention.

Looks only at the names directly inside each dir (entries' contents aren't
checked), skipping ".mynew-suffix" and a "temp" dir. A sidecar belongs to the
dir named like its "<yy><seq>[-suffix]" prefix; names are compared ignoring
case, so "260001-nry" and "260001-NRY" count as two dirs for one sidecar.
Read-only.
"""

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import entries as ent

MAJOR, MEDIUM, MINOR = "major", "medium", "minor"
SEVERITIES = (MAJOR, MEDIUM, MINOR)  # most severe first

SUFFIX_FILE = ".mynew-suffix"
TEMP_DIR = "temp"

# the last word of a description this short is likely a tag without its "@"
SHORT_WORD_RE = re.compile(r"^[A-Za-z]{2,3}$")


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str  # stable id for scripts, e.g. "no-sidecar"
    name: str  # the file or dir inside the Everything dir it's about
    message: str

    def to_json(self) -> dict:
        return {"severity": self.severity, "code": self.code, "name": self.name,
                "message": self.message}


@dataclass
class DirResult:
    dir: str
    findings: list[Finding] = field(default_factory=list)

    def shown(self, levels: tuple[str, ...]) -> list[Finding]:
        order = {s: i for i, s in enumerate(SEVERITIES)}
        return sorted((f for f in self.findings if f.severity in levels),
                      key=lambda f: (order[f.severity], f.name, f.code))


def _scan(d: str) -> tuple[list[str], list[str]] | None:
    """(dir names, other names) directly inside `d`, minus the skipped ones; None if unreadable.

    Symlinks count as what they point to; a broken one counts as a file.
    """
    dirs, files = [], []
    try:
        with os.scandir(d) as it:
            for e in it:
                if e.is_dir():
                    if e.name != TEMP_DIR:
                        dirs.append(e.name)
                elif e.name != SUFFIX_FILE:
                    files.append(e.name)
    except OSError:
        return None
    return sorted(dirs), sorted(files)


def _check_names(dirs: list[str], files: list[str]
                 ) -> tuple[list[Finding], list[str], list[ent.SidecarName]]:
    """Naming findings, plus the entry dirs and sidecars that passed."""
    findings, entry_dirs, sidecars = [], [], []
    for name in dirs:
        if ent.parse_entry(name):
            entry_dirs.append(name)
        elif ent.parse_sidecar(name):
            findings.append(Finding(MAJOR, "dir-name-not-id", name,
                                    "dir name has more than the <yy><seq>[-suffix] id"))
        else:
            findings.append(Finding(MAJOR, "bad-dir-name", name,
                                    "dir name isn't a <yy><seq>[-suffix] id"))
    for name in files:
        sidecar = ent.parse_sidecar(name)
        if sidecar:
            sidecars.append(sidecar)
        else:
            findings.append(Finding(MAJOR, "bad-file-name", name,
                                    "file name doesn't start with <yy><seq>[-suffix]_<description>"))
    return findings, entry_dirs, sidecars


def _check_pairs(entry_dirs: list[str], sidecars: list[ent.SidecarName]) -> list[Finding]:
    """Dirs without exactly one sidecar, sidecars without exactly one dir, non-.md sidecars."""
    findings = []
    dirs_by_key = defaultdict(list)
    for name in entry_dirs:
        dirs_by_key[name.casefold()].append(name)
    sidecars_by_key = defaultdict(list)
    for s in sidecars:
        sidecars_by_key[s.entry.name.casefold()].append(s.name)

    for name in entry_dirs:
        found = sidecars_by_key[name.casefold()]
        if not found:
            findings.append(Finding(MAJOR, "no-sidecar", name, "dir has no sidecar file"))
        elif len(found) > 1:
            findings.append(Finding(MEDIUM, "several-sidecars", name,
                                    f"dir has {len(found)} sidecar files: " + ", ".join(found)))

    for s in sidecars:
        found = dirs_by_key[s.entry.name.casefold()]
        if not found:
            msg = f"no dir {s.entry.name} for this sidecar"
            # same number, other suffix: probably a typo in one of them
            near = [d for d in entry_dirs if ent.parse_entry(d).id == s.entry.id]
            if near:
                msg += " (same id: " + ", ".join(near) + ")"
            findings.append(Finding(MEDIUM, "orphan-sidecar", s.name, msg))
        elif len(found) > 1:
            findings.append(Finding(MAJOR, "several-dirs", s.name,
                                    f"{len(found)} dirs match this sidecar: " + ", ".join(found)))
        if s.ext != ".md":
            ext = f"extension {s.ext}" if s.ext else "no extension"
            findings.append(Finding(MEDIUM, "not-md", s.name, f"sidecar has {ext}, not .md"))
    return findings


def _tags(s: ent.SidecarName) -> tuple[str, ...]:
    """Tags of a sidecar, including those in "<id>_@a_@b" where parsing takes "@a" as description."""
    if s.description.startswith("@"):
        return tuple(s.description[1:].split("_@")) + s.tags
    return s.tags


def _check_minor(sidecars: list[ent.SidecarName], known_tags: set[str],
                 preferred: dict[str, str]) -> list[Finding]:
    """Slips in descriptions and tags (hidden unless all levels are shown)."""
    findings = []
    for s in sidecars:
        all_tags = _tags(s)
        tags = [t for t in all_tags if t.strip()]
        if not tags:
            findings.append(Finding(MINOR, "no-tags", s.name, "sidecar has no tags"))
        if len(tags) < len(all_tags):
            findings.append(Finding(MINOR, "empty-tag", s.name, "sidecar has an empty tag (\"_@\")"))

        if not s.description or s.description.startswith("@"):
            findings.append(Finding(MINOR, "no-description", s.name,
                                    "sidecar has no description, only tags"))
        else:
            words = s.description.split("_")
            last = words[-1]
            if len(words) > 1 and (last.casefold() in known_tags or SHORT_WORD_RE.match(last)):
                findings.append(Finding(MINOR, "tag-without-at", s.name,
                                        f"description ends in \"_{last}\", maybe meant as tag @{last}"))

        for t in tags:
            if t != t.rstrip():
                findings.append(Finding(MINOR, "tag-trailing-space", s.name,
                                        f"tag @{t} ends in a space"))
            want = preferred[t.strip().casefold()]
            if t.strip() != want:
                findings.append(Finding(MINOR, "tag-case", s.name,
                                        f"tag @{t.strip()} is usually spelled @{want}"))
    return findings


def check(dirs: list[str]) -> list[DirResult]:
    """Findings for each of `dirs`. Tag spellings are compared across all of them. Read-only."""
    scanned = {}
    tag_spellings = defaultdict(Counter)  # casefolded tag -> spelling -> sidecars using it
    for d in dirs:
        scanned[d] = _scan(d)
        if scanned[d] is None:
            continue
        for name in scanned[d][1]:
            s = ent.parse_sidecar(name)
            for t in (_tags(s) if s else ()):
                if t.strip():
                    tag_spellings[t.strip().casefold()][t.strip()] += 1
    # most used spelling wins, ties go to the alphabetically first
    preferred = {key: min(c, key=lambda sp: (-c[sp], sp)) for key, c in tag_spellings.items()}

    results = []
    for d in dirs:
        result = DirResult(d)
        results.append(result)
        if scanned[d] is None:
            result.findings.append(Finding(MAJOR, "unreadable", ".", "can't read this dir"))
            continue
        findings, entry_dirs, sidecars = _check_names(*scanned[d])
        findings += _check_pairs(entry_dirs, sidecars)
        findings += _check_minor(sidecars, set(tag_spellings), preferred)
        result.findings = findings
    return results
