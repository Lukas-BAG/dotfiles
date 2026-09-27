"""`everything` command: subcommands over all Everything dirs."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter

from . import check, discovery, entries, goto, new, stats


def _tilde(path: str, home: str) -> str:
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _discover(args: argparse.Namespace, prog: str) -> tuple[str, list[str]] | None:
    """Resolve --root and find its Everything dirs; print an error and return None if none."""
    home = os.path.expanduser("~")
    if not os.path.isdir(args.root):
        print(f"{prog}: not a dir: {args.root}", file=sys.stderr)
        return None
    root = os.path.realpath(args.root)
    dirs, age = discovery.find_everything_dirs_cached(root, home, fresh=args.fresh,
                                                     network=args.network)
    if age is not None:
        print(f"{prog}: using cached Everything-dir list ({_age(age)} old; "
              "--fresh to re-search)", file=sys.stderr)
    if not dirs:
        print(f"{prog}: no Everything dirs under {_tilde(root, home)} (try --root <path>)",
              file=sys.stderr)
        return None
    return root, dirs


def _table(dirs: list[str], home: str, bookmark: str | None) -> tuple[str, list[str], int]:
    """(header, one aligned row per dir, nested count) as shown by `everything list`."""
    rows = []
    nested = 0
    for d in dirs:
        depth = sum(d.startswith(other + "/") for other in dirs)
        nested += depth > 0

        found = entries.list_entries(d)
        newest = "-"
        if found:
            # first one wins on equal ids, e.g. "260001" before "260001-a"
            newest = max(found, key=lambda e: e.id).name
        suffixes = list(dict.fromkeys(e.suffix or "(none)" for e in found))

        display = _tilde(d, home) if d != home else d
        if depth:
            display = " " * (depth * 2) + "└ " + display
        display = ("* " if d == bookmark else "  ") + display
        rows.append((display, len(found), newest, ",".join(suffixes) or "-"))

    width = max([4] + [len(r[0]) for r in rows])
    header = f"  {'PATH':<{width - 2}}  {'ENTRIES':>7}  {'NEWEST':<12}  SUFFIX"
    lines = [f"{display:<{width}}  {count:>7}  {newest:<12}  {suffixes}"
             for display, count, newest, suffixes in rows]
    return header, lines, nested


def cmd_list(args: argparse.Namespace) -> int:
    """List every Everything dir with entry count, newest entry and suffixes."""
    found = _discover(args, "everything list")
    if found is None:
        return 1
    root, dirs = found
    if args.paths:
        print("\n".join(dirs))
        return 0

    home = os.path.expanduser("~")
    bookmark = discovery.bookmark_dir()
    header, lines, nested = _table(dirs, home, bookmark)
    print(f"Everything dirs under {_tilde(root, home)} ({len(dirs)} found, {nested} nested)")
    print()
    print(header)
    print("\n".join(lines))
    if bookmark in dirs:
        print()
        print("* = bashmark 'e' (used by ge)")
    return 0


def _matching(dirs: list[str], text: str | None) -> list[str]:
    """Dirs whose path contains `text` (any case); all of them if there's no text."""
    needle = text.casefold() if text else None
    return [d for d in dirs if needle is None or needle in d.casefold()]


def cmd_pick(args: argparse.Namespace) -> int:
    """Print one Everything dir, matched by text and/or picked with fzf."""
    prog = "everything pick"
    found = _discover(args, prog)
    if found is None:
        return 1
    _, dirs = found

    matches = _matching(dirs, args.text)
    if not matches:
        print(f"{prog}: no Everything dir matching '{args.text}'", file=sys.stderr)
        return 1
    if len(matches) == 1:
        what = f"matching '{args.text}'" if args.text else "found"
        # stderr, so $(everything pick) still only captures the path
        print(f"{prog}: only one Everything dir {what}, picking it without fzf",
              file=sys.stderr)
        print(matches[0])
        return 0

    home = os.path.expanduser("~")
    # table over all dirs so nesting and column widths match `everything list`
    header, lines, _ = _table(dirs, home, discovery.bookmark_dir())
    rows = [f"{line}\t{d}" for d, line in zip(dirs, lines) if d in matches]

    if shutil.which("fzf") is None:
        print(f"{prog}: fzf not found and several dirs match, refusing:", file=sys.stderr)
        print("\n".join(_tilde(d, home) for d in matches), file=sys.stderr)
        return 1
    prompt = f"multiple matches for '{args.text}' > " if args.text else "Everything dir > "
    # fzf draws its UI on /dev/tty, so this also works inside $(...)
    res = subprocess.run(
        ["fzf", "--delimiter=\t", "--with-nth=1", "--layout=reverse",
         f"--header={header}", f"--prompt={prompt}"],
        input="\n".join(rows) + "\n", stdout=subprocess.PIPE, text=True)
    pick = res.stdout.rstrip("\n").split("\t")[-1] if res.returncode == 0 else ""
    if not pick:
        print(f"{prog}: no selection made, refusing", file=sys.stderr)
        return 1
    print(pick)
    return 0


def cmd_goto(args: argparse.Namespace) -> int:
    """Print one entry dir, found by id or sidecar text, for ge/gel to cd into."""
    prog = args.label
    home = os.path.expanduser("~")
    if args.all:
        found = _discover(args, prog)
        if found is None:
            return 1
        bases = found[1]
    elif args.dir:
        if not os.path.isdir(args.dir):
            print(f"{prog}: not a dir: {args.dir}", file=sys.stderr)
            return 1
        bases = [args.dir]
    else:
        bookmark = discovery.bookmark_dir()
        if bookmark is None:
            print(f"{prog}: bashmark 'e' is not set to a valid dir (set it with: s e)",
                  file=sys.stderr)
            return 1
        bases = [bookmark]

    if args.query is None:
        if args.all:
            print(f"{prog}: --all needs an id or text (to pick an Everything dir, use cde)",
                  file=sys.stderr)
            return 1
        print(bases[0])  # plain `ge`: go to the Everything dir itself
        return 0

    if args.query.isdigit():
        try:
            reason = goto.id_prefix(args.query, args.year)
        except ValueError:
            print(f"{prog}: invalid year '{args.year}' - use e.g. 25 or 2025", file=sys.stderr)
            return 1
        matches = [(b, e) for b in bases for e in goto.find_by_id(b, reason)]
    else:
        if args.year is not None:
            print(f"{prog}: a year only goes with a numeric id (quote text with spaces)",
                  file=sys.stderr)
            return 1
        reason = args.query
        matches = [(b, e) for b in bases for e in goto.find_by_text(b, reason)]

    where = " in any Everything dir" if args.all else ""
    if not matches:
        print(f"{prog}: no entry matching '{reason}' found{where}", file=sys.stderr)
        return 1

    def label(base: str, entry: str, width: int = 0) -> str:
        name = goto.sidecar_name(entry)
        return f"{name:<{width}}  {_tilde(base, home)}" if args.all else name

    if len(matches) == 1:
        (base, entry), = matches
        # stderr, so $(everything goto) still only captures the path
        print(f"{prog}: {label(base, entry)}", file=sys.stderr)
        print(entry)
        return 0

    if shutil.which("fzf") is None:
        print(f"{prog}: multiple entries match '{reason}', refusing:", file=sys.stderr)
        print("\n".join(f"  {e}" for _, e in matches), file=sys.stderr)
        return 1
    width = max(len(goto.sidecar_name(e)) for _, e in matches)
    rows = [f"{label(b, e, width)}\t{e}" for b, e in matches]
    # fzf draws its UI on /dev/tty, so this also works inside $(...)
    res = subprocess.run(
        ["fzf", "--delimiter=\t", "--with-nth=1",
         f"--prompt={prog}: multiple matches for '{reason}'{where} > "],
        input="\n".join(rows) + "\n", stdout=subprocess.PIPE, text=True)
    pick = res.stdout.rstrip("\n").split("\t")[-1] if res.returncode == 0 else ""
    if not pick:
        print(f"{prog}: no selection made, refusing", file=sys.stderr)
        return 1
    print(pick)
    return 0


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else plural or word + 's'}"


def _render_stats(st: stats.Stats, title: str) -> list[str]:
    """Text sections for one Stats, as shown by `everything stats`."""
    lines = [f"{title}  ({_plural(len(st.dirs), 'dir')}, "
             f"{_plural(st.entries, 'entry', 'entries')}, {_plural(st.sidecars, 'sidecar')})", ""]

    distinct = str(len(st.ids))
    shared = st.shared_ids
    if len(shared) == 1:
        ((d, i), sufs), = shared.items()
        where = f" in {_tilde(d, os.path.expanduser('~'))}" if len(st.dirs) > 1 else ""
        distinct += f"   ({i} is used by {len(sufs)} suffixes{where}: {', '.join(sufs)})"
    elif shared:
        distinct += f"   ({len(shared)} ids are used by several suffixes in the same dir)"
    by_year = "   ".join(f"20{yy}: {n}" for yy, n in sorted(st.by_year.items())) or "-"
    lines += ["ENTRIES",
              f"  total entries   {st.entries}",
              f"  distinct ids    {distinct}",
              f"  by year         {by_year}", ""]

    lines.append("SUFFIXES")
    rows = []
    for name, s in st.sorted_suffixes():
        ranges = "   ".join(f"{yy}: {lo}" if lo == hi else f"{yy}: {lo}–{hi}"
                           for yy, (lo, hi) in sorted(s.ranges.items()))
        rows.append((name, s.entries, ranges))
    if rows:
        width = max(len("SUFFIX"), *(len(r[0]) for r in rows))
        lines.append(f"  {'SUFFIX':<{width}}  {'ENTRIES':>7}  RANGE")
        lines += [f"  {name:<{width}}  {n:>7}  {ranges}" for name, n, ranges in rows]
    else:
        lines.append("  -")
    lines.append("")

    lines.append(f"TAGS  ({st.tagged_sidecars} of {_plural(st.sidecars, 'sidecar')} tagged"
                 + (f", {len(st.tags)} distinct)" if st.tags else ")"))
    tags = st.sorted_tags()
    if tags:
        width = max(len(t) for t, _ in tags) + 1
        lines += [f"  {'@' + t:<{width}}  {n:>4}" for t, n in tags]
    else:
        lines.append("  -")
    return lines


def cmd_stats(args: argparse.Namespace) -> int:
    """Counts, id ranges per suffix and tag usage across Everything dirs."""
    prog = "everything stats"
    found = _discover(args, prog)
    if found is None:
        return 1
    root, dirs = found
    dirs = _matching(dirs, args.dir)
    if not dirs:
        print(f"{prog}: no Everything dir matching '{args.dir}'", file=sys.stderr)
        return 1

    home = os.path.expanduser("~")
    if args.per_dir:
        results = [(f"Everything stats for {_tilde(d, home)}", stats.collect([d])) for d in dirs]
    else:
        title = f"Everything stats under {_tilde(root, home)}"
        if args.dir:
            title += f" matching '{args.dir}'"
        results = [(title, stats.collect(dirs))]

    if args.json:
        data = [st.to_json() for _, st in results]
        print(json.dumps(data if args.per_dir else data[0], indent=2, ensure_ascii=False))
        return 0
    blocks = ["\n".join(_render_stats(st, title)) for title, st in results]
    print("\n\n".join(blocks))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Report naming problems in Everything dirs; exit 1 if any are shown."""
    prog = "everything check"
    found = _discover(args, prog)
    if found is None:
        return 1
    root, dirs = found
    dirs = _matching(dirs, args.dir)
    if not dirs:
        print(f"{prog}: no Everything dir matching '{args.dir}'", file=sys.stderr)
        return 1

    levels = check.SEVERITIES if args.all_levels else (check.MAJOR, check.MEDIUM)
    results = check.check(dirs)
    shown = [(r.dir, r.shown(levels)) for r in results]
    counts = Counter(f.severity for _, fs in shown for f in fs)
    hidden = sum(f.severity not in levels for r in results for f in r.findings)

    if args.json:
        print(json.dumps({
            "dirs": [{"dir": d, "findings": [f.to_json() for f in fs]} for d, fs in shown],
            "counts": {s: counts[s] for s in levels},
            "hidden": hidden,
        }, indent=2, ensure_ascii=False))
        return 1 if counts else 0

    home = os.path.expanduser("~")
    blocks = []
    for d, fs in shown:
        if not fs:
            continue
        lines = [_tilde(d, home) if d != home else d]
        # capped so one long sidecar name doesn't push every message far right
        width = min(40, max(len(f.name) for f in fs))
        for severity in levels:
            group = [f for f in fs if f.severity == severity]
            if group:
                lines.append(f"  {severity.upper()}")
                lines += [f"    {f.name:<{width}}  {f.message}" for f in group]
        blocks.append("\n".join(lines))
    if blocks:
        print("\n\n".join(blocks))
        print()

    clean = sum(not fs for _, fs in shown)
    summary = f"{_plural(len(dirs), 'Everything dir')} checked"
    if args.dir:
        summary += f" (matching '{args.dir}')"
    summary += f" under {_tilde(root, home)}: "
    summary += ", ".join(f"{counts[s]} {s}" for s in levels)
    summary += f"; {clean} clean"
    if hidden:
        summary += f" ({hidden} minor hidden, --all-levels to show)"
    print(summary)
    return 1 if counts else 0


def cmd_new(args: argparse.Namespace) -> int:
    """Create the next entry dir + sidecar and print its path, for mynew to cd into."""
    prog = args.label
    d = os.path.abspath(args.dir)
    if not os.path.isdir(d):
        print(f"{prog}: not a dir: {args.dir}", file=sys.stderr)
        return 1
    try:
        suffix = new.read_suffix(d)
        save = None
        if suffix is None:
            # plan once first, so a non-Everything dir is refused before asking
            new.plan(d, args.description, args.tags, "", new.current_year())
            # everything but the new path goes to stderr: mynew runs this in $(...)
            print(f"{prog}: no suffix configured for {d} yet.", file=sys.stderr)
            print("Suffix to use for new entries here (leave blank for none): ",
                  end="", file=sys.stderr, flush=True)
            answer = sys.stdin.readline()
            if not answer:
                print(file=sys.stderr)
                raise new.Refusal("no answer, refusing (nothing saved)")
            suffix = save = new.check_suffix(answer)
        p = new.plan(d, args.description, args.tags, suffix, new.current_year())
    except new.Refusal as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1

    if p.rolled_over:
        print(f"{prog}: year prefix rolled over (highest existing entry is '{p.rolled_over}', "
              f"current year is '{p.entry[:2]}') - starting sequence over at 0001",
              file=sys.stderr)
    words = new.snake(args.description).split("_")
    if len(words) > 1 and check.SHORT_WORD_RE.match(words[-1]):
        print(f"{prog}: note: description ends in \"_{words[-1]}\", did you mean tag "
              f"@{words[-1]}? (tags go after the quoted description)", file=sys.stderr)
    try:
        path = new.create(p, save)
    except OSError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1
    print(f"{prog}: created '{p.entry}/' with sidecar '{p.sidecar}'", file=sys.stderr)
    print(path)
    return 0


# searched when --root isn't given (tests point this at a fake tree)
DEFAULT_ROOT = "/"
NETWORK_HELP = ("also search network filesystems (nfs, cifs, sshfs, ...); local mounts are "
                "always searched")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="everything", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("list", help="list every Everything dir (alias: lse)",
                       description="List every Everything or Everything-like dir (name "
                       "contains \"everything\", any case) with its \"<yy><seq>[-suffix]\" "
                       "entry count, newest entry and suffixes. Dirs nested in another "
                       "listed dir are indented; \"*\" marks the \"e\" bashmark used by ge. "
                       "Read-only.")
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help="where to search (default: /)")
    p.add_argument("--network", action="store_true", help=NETWORK_HELP)
    p.add_argument("--fresh", action="store_true",
                   help="ignore the cached Everything-dir list (up to 6 h old) and search again "
                        "(no-op while the cache is disabled)")
    p.add_argument("--paths", action="store_true",
                   help="print bare absolute paths only, one per line (for scripting)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("pick", help="print one Everything dir, via text match or fzf (used by cde)",
                       description="Print the absolute path of one Everything dir. Without "
                       "TEXT, consider all of them; with TEXT, only dirs whose path "
                       "contains it (any case). A single candidate is printed directly, "
                       "several are offered in fzf. Read-only; cde wraps this to cd there.")
    p.add_argument("text", nargs="?", help="case-insensitive substring of the path")
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help="where to search (default: /)")
    p.add_argument("--network", action="store_true", help=NETWORK_HELP)
    p.add_argument("--fresh", action="store_true",
                   help="ignore the cached Everything-dir list (up to 6 h old) and search again "
                        "(no-op while the cache is disabled)")
    p.set_defaults(func=cmd_pick)

    p = sub.add_parser("goto", help="print one entry dir, by id or sidecar text (used by ge/gel)",
                       description="Print the path of one \"<yy><seq>[-suffix]\" entry dir. "
                       "A numeric QUERY is an id (\"1\" -> \"<yy>0001\", YEAR as 25 or 2025, "
                       "default this year); other text is matched against sidecar file names "
                       "(any case). Searches the \"e\" bashmark dir by default. A single match "
                       "is printed directly, several are offered in fzf; without QUERY the "
                       "searched dir itself is printed. Read-only; ge and gel wrap this to cd there.")
    p.add_argument("query", nargs="?", help="numeric id or case-insensitive text")
    p.add_argument("year", nargs="?", help="year of a numeric id, e.g. 25 or 2025")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("-a", "--all", action="store_true",
                       help="search every Everything dir under --root, not just the bashmark")
    scope.add_argument("--dir", help="search this dir instead of the bashmark (used by gel)")
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help="where --all searches for Everything dirs (default: /)")
    p.add_argument("--network", action="store_true", help=NETWORK_HELP)
    p.add_argument("--fresh", action="store_true",
                   help="with --all, ignore the cached Everything-dir list (no-op while the "
                        "cache is disabled)")
    p.add_argument("--label", default="everything goto", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_goto)

    p = sub.add_parser("stats", help="entry, suffix and tag statistics across Everything dirs",
                       description="Statistics over the \"<yy><seq>[-suffix]\" entries and "
                       "\"<entry>_<description>[_@tag...]\" sidecars of every Everything dir: "
                       "entry counts (total, distinct ids, per year), entries and id range per "
                       "suffix (split by year, since seq restarts every year) and tag usage. "
                       "One combined total by default. Read-only; problems such as missing "
                       "sidecars are left to the checker.")
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help="where to search (default: /)")
    p.add_argument("--network", action="store_true", help=NETWORK_HELP)
    p.add_argument("--fresh", action="store_true",
                   help="ignore the cached Everything-dir list (up to 6 h old) and search again "
                        "(no-op while the cache is disabled)")
    p.add_argument("--dir", metavar="TEXT",
                   help="only Everything dirs whose path contains TEXT (any case), as in pick")
    p.add_argument("--per-dir", action="store_true",
                   help="separate stats for each Everything dir instead of one combined total")
    p.add_argument("--json", action="store_true",
                   help="print JSON instead of text (a list of objects with --per-dir)")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("check", help="report naming problems in Everything dirs",
                       description="Check the names directly inside every Everything dir "
                       "(skipping \".mynew-suffix\" and \"temp\"): every dir must be a "
                       "\"<yy><seq>[-suffix]\" id with exactly one "
                       "\"<id>_<description>[_@tag...].md\" sidecar, and every other file "
                       "must be such a sidecar with exactly one dir. Problems are major or "
                       "medium; minor slips (no tags, a tag without \"@\", tags differing "
                       "only in case, ...) only show with --all-levels. Exits 1 if anything "
                       "is shown. Read-only.")
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help="where to search (default: /)")
    p.add_argument("--network", action="store_true", help=NETWORK_HELP)
    p.add_argument("--fresh", action="store_true",
                   help="ignore the cached Everything-dir list (up to 6 h old) and search again "
                        "(no-op while the cache is disabled)")
    p.add_argument("--dir", metavar="TEXT",
                   help="only Everything dirs whose path contains TEXT (any case), as in pick")
    p.add_argument("--all-levels", action="store_true",
                   help="also show minor findings")
    p.add_argument("--json", action="store_true",
                   help="print JSON instead of text")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("new", help="create the next entry dir + sidecar (used by mynew)",
                       description="Create the next \"<yy><seq>[-suffix]\" entry dir in an "
                       "Everything dir (the current dir by default) plus its empty sidecar "
                       "\"<entry>_<description>[_@tag...].md\", and print the new dir's "
                       "path. seq is one past the highest id there and restarts at 0001 in a "
                       "new year. The suffix is asked once per dir and saved in its "
                       "\".mynew-suffix\". Refuses in a dir without entries and never "
                       "overwrites anything. mynew wraps this to cd there.")
    p.add_argument("description", help="free text, turned into lower_snake_case")
    p.add_argument("tags", nargs="*", metavar="tag", help="tags, with or without a leading @")
    p.add_argument("--dir", default=".", help="the Everything dir (default: current dir)")
    p.add_argument("--label", default="everything new", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_new)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:  # e.g. `everything list --paths | head -1`
        sys.stderr.close()
        return 0
