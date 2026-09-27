"""Statistics over the entries and sidecars of Everything dirs.

Only counts what is there. Anything that looks wrong (id gaps, missing or
orphaned sidecars, tags without "@") is left to the correctness checker.
"""

from collections import Counter
from dataclasses import dataclass, field

from . import entries as ent

NO_SUFFIX = "(none)"  # can't clash with a real suffix, those are [A-Za-z0-9]+


@dataclass
class SuffixStats:
    entries: int = 0
    # yy -> (lowest seq, highest seq); seq restarts every year
    ranges: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass
class Stats:
    dirs: list[str]
    entries: int = 0
    sidecars: int = 0
    tagged_sidecars: int = 0
    by_year: Counter = field(default_factory=Counter)  # yy -> entries
    suffixes: dict[str, SuffixStats] = field(default_factory=dict)
    ids: dict[int, set[str]] = field(default_factory=dict)  # id -> suffixes using it
    tags: Counter = field(default_factory=Counter)  # tag -> sidecars carrying it

    @property
    def shared_ids(self) -> dict[int, list[str]]:
        """Ids used by more than one suffix, e.g. {260001: ["gel", "nry"]}."""
        return {i: sorted(s) for i, s in sorted(self.ids.items()) if len(s) > 1}

    def sorted_suffixes(self) -> list[tuple[str, SuffixStats]]:
        """Most entries first, then by name."""
        return sorted(self.suffixes.items(), key=lambda kv: (-kv[1].entries, kv[0]))

    def sorted_tags(self) -> list[tuple[str, int]]:
        """Most used first, then by name."""
        return sorted(self.tags.items(), key=lambda kv: (-kv[1], kv[0]))

    def to_json(self) -> dict:
        return {
            "dirs": self.dirs,
            "entries": self.entries,
            "sidecars": self.sidecars,
            "distinct_ids": len(self.ids),
            "shared_ids": {str(i): s for i, s in self.shared_ids.items()},
            "by_year": {"20" + yy: n for yy, n in sorted(self.by_year.items())},
            "suffixes": {
                name: {"entries": s.entries,
                       "ranges": {"20" + yy: list(r) for yy, r in sorted(s.ranges.items())}}
                for name, s in self.sorted_suffixes()},
            "tagged_sidecars": self.tagged_sidecars,
            "tags": dict(self.sorted_tags()),
        }


def collect(dirs: list[str]) -> Stats:
    """Stats over the entries and sidecars directly inside each of `dirs`. Read-only."""
    stats = Stats(dirs=list(dirs))
    for d in dirs:
        for e in ent.list_entries(d):
            stats.entries += 1
            stats.by_year[e.yy] += 1
            suffix = e.suffix or NO_SUFFIX
            s = stats.suffixes.setdefault(suffix, SuffixStats())
            s.entries += 1
            lo, hi = s.ranges.get(e.yy, (e.seq, e.seq))
            s.ranges[e.yy] = (min(lo, e.seq), max(hi, e.seq))
            stats.ids.setdefault(e.id, set()).add(suffix)

        for sidecar in ent.list_sidecars(d):
            stats.sidecars += 1
            tags = set(sidecar.tags) - {""}  # "_@" with nothing after it isn't a tag
            stats.tagged_sidecars += bool(tags)
            stats.tags.update(tags)
    return stats
