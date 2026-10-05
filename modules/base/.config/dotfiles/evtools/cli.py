"""`everything` command: subcommands over all Everything dirs."""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from collections import Counter

from . import check, discovery, entries, goto, info, new, prompt, remove, rename, saved_locations, stats


def _tilde(path: str, home: str) -> str:
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _saved_dirs(prog: str) -> list[str] | None:
    """The saved Everything dirs (plus the "e" bashmark dir), or None after an error message.

    Saved dirs that no longer exist are skipped with a warning; no list yet is an error.
    """
    home = os.path.expanduser("~")
    try:
        found = saved_locations.all_dirs()
    except saved_locations.NotSetUp as e:
        print(f"{prog}: no list of Everything dirs yet - {e}", file=sys.stderr)
        return None
    for d in found.missing:
        print(f"{prog}: warning: saved dir {_tilde(d, home)} no longer exists, skipping",
              file=sys.stderr)
    if not found.dirs:
        print(f"{prog}: no Everything dirs in the list - add some with `everything scan` "
              "or `everything mark`", file=sys.stderr)
        return None
    return found.dirs


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
        if discovery.is_link(d):
            target = os.path.realpath(d)
            display += " → " + (_tilde(target, home) if target != home else target)
        if depth:
            display = " " * (depth * 2) + "└ " + display
        display = ("* " if bookmark and os.path.realpath(d) == bookmark else "  ") + display
        rows.append((display, len(found), newest, ",".join(suffixes) or "-"))

    width = max([4] + [len(r[0]) for r in rows])
    header = f"  {'PATH':<{width - 2}}  {'ENTRIES':>7}  {'NEWEST':<12}  SUFFIX"
    lines = [f"{display:<{width}}  {count:>7}  {newest:<12}  {suffixes}"
             for display, count, newest, suffixes in rows]
    return header, lines, nested


def cmd_list(args: argparse.Namespace) -> int:
    """List every Everything dir with entry count, newest entry and suffixes."""
    dirs = _saved_dirs("everything list")
    if dirs is None:
        return 1
    if args.paths:
        print("\n".join(dirs))
        return 0

    home = os.path.expanduser("~")
    bookmark = discovery.bookmark_dir()
    header, lines, nested = _table(dirs, home, bookmark)
    links = sum(map(discovery.is_link, dirs))
    counts = f"{_plural(len(dirs), 'dir')}, {nested} nested" + (f", {_plural(links, 'symlink')}"
                                                        if links else "")
    print(f"Saved Everything dirs ({counts})")
    print()
    print(header)
    print("\n".join(lines))
    if bookmark and any(os.path.realpath(d) == bookmark for d in dirs):
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
    dirs = _saved_dirs(prog)
    if dirs is None:
        return 1

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
        bases = _saved_dirs(prog)
        if bases is None:
            return 1
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


def _human_size(n: int) -> str:
    """Bytes as e.g. "512B", "1.5K", "23M" (powers of 1024, like `du -h`)."""
    for unit in "BKMGT":
        if n < 1024 or unit == "T":
            break
        n /= 1024
    if unit == "B":
        return f"{n}B"
    return f"{n:.1f}{unit}" if n < 10 else f"{n:.0f}{unit}"


def cmd_entries(args: argparse.Namespace) -> int:
    """List the entries of the Everything dir you're in (or of all of them)."""
    prog = "everything entries"
    home = os.path.expanduser("~")
    if args.all:
        dirs = _saved_dirs(prog)
        if dirs is None:
            return 1
        title = "Entries of every saved Everything dir"
    else:
        d = discovery.enclosing_everything_dir(os.getcwd(), saved_locations.load())
        if d is None:
            print(f"{prog}: not inside an Everything dir (cd into one, or use --all)",
                  file=sys.stderr)
            return 1
        dirs = [d]
        title = f"Entries of {_tilde(d, home) if d != home else d}"

    rows = []
    for d in dirs:
        sidecars = entries.sidecars_by_entry(d)
        for e in entries.list_entries(d):
            # several sidecars per entry are a checker problem; show the first
            s = sidecars.get(e.name, [None])[0]
            size = entries.tree_size(os.path.join(d, e.name)) if args.size else 0
            rows.append((e, d, s, size))
    # by id, then full name so suffixes of one id sit together; dir breaks ties
    rows.sort(key=lambda r: (r[0].id, r[0].name, r[1]))
    if args.size:
        rows.sort(key=lambda r: r[3], reverse=True)  # stable: ties keep the id order

    table = []
    for e, d, s, size in rows:
        row = [e.name]
        if args.size:
            row.append(_human_size(size))
        row += [s.description if s else "", " ".join("@" + t for t in s.tags) if s else ""]
        if args.all:
            row.append(_tilde(d, home) if d != home else d)
        table.append(row)
    header = ["ENTRY"] + (["SIZE"] if args.size else []) + ["DESCRIPTION", "TAGS"]
    header += ["EVERYTHING DIR"] if args.all else []

    count = _plural(len(rows), "entry", "entries")
    if args.all:
        count += f" in {_plural(len(dirs), 'dir')}"
    if args.size:
        count += f", {_human_size(sum(r[3] for r in rows))} total"
    print(f"{title} ({count})")
    if not rows:
        return 0
    print()
    widths = [max(len(r[i]) for r in [header] + table) for i in range(len(header))]
    size_col = 1 if args.size else None
    for r in [header] + table:
        cells = [c.rjust(w) if i == size_col else c.ljust(w)
                 for i, (c, w) in enumerate(zip(r, widths))]
        print("  ".join(cells).rstrip())
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
    dirs = _saved_dirs(prog)
    if dirs is None:
        return 1
    dirs = _matching(dirs, args.dir)
    if not dirs:
        print(f"{prog}: no Everything dir matching '{args.dir}'", file=sys.stderr)
        return 1

    home = os.path.expanduser("~")
    if args.per_dir:
        results = [(f"Everything stats for {_tilde(d, home)}", stats.collect([d])) for d in dirs]
    else:
        title = "Everything stats for the saved dirs"
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
    dirs = _saved_dirs(prog)
    if dirs is None:
        return 1
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
    summary += ": "
    summary += ", ".join(f"{counts[s]} {s}" for s in levels)
    summary += f"; {clean} clean"
    if hidden:
        summary += f" ({hidden} minor hidden, --all-levels to show)"
    print(summary)
    return 1 if counts else 0


def _ask_suffix(prog: str, d: str) -> str:
    # everything but the new path goes to stderr: mynew runs this in $(...)
    print(f"{prog}: no suffix configured for {d} yet.", file=sys.stderr)
    print("Suffix to use for new entries here (leave blank for none): ",
          end="", file=sys.stderr, flush=True)
    answer = sys.stdin.readline()
    if not answer:
        print(file=sys.stderr)
        raise new.Refusal("no answer, refusing (nothing saved)")
    return new.check_suffix(answer)


def _pick_suffix(args: argparse.Namespace, prog: str, d: str) -> tuple[str, str | None]:
    """(suffix to use, suffix to save to .mynew-suffix or None).

    --suffix: used once, nothing saved. --ask-suffix: asked, replaces the saved one.
    Otherwise the saved one wins, then ~/.devbox_id (saved), then ask (saved).
    """
    if args.suffix is not None:
        return new.check_suffix(args.suffix), None
    if args.ask_suffix:
        suffix = _ask_suffix(prog, d)
        return suffix, suffix
    suffix = new.read_suffix(d)
    if suffix is not None:
        return suffix, None
    suffix = new.read_devbox_suffix(os.path.expanduser("~"))
    if suffix is not None:
        print(f"{prog}: using suffix '{suffix}' from ~/{new.DEVBOX_FILE}", file=sys.stderr)
        return suffix, suffix
    suffix = _ask_suffix(prog, d)
    return suffix, suffix


def cmd_new(args: argparse.Namespace) -> int:
    """Create the next entry dir + sidecar and print its path, for mynew to cd into."""
    prog = args.label
    d = os.path.abspath(args.dir)
    if not os.path.isdir(d):
        print(f"{prog}: not a dir: {args.dir}", file=sys.stderr)
        return 1
    year = new.current_year()
    try:
        if args.ask_suffix and not sys.stdin.isatty():
            raise new.Refusal("--ask-suffix needs a terminal to ask on, refusing")
        # plan once first, so a bad dir is refused before anything is asked
        new.plan(d, args.description, args.tags, "", year)
        if not new.entries.list_entries(d):
            name = os.path.basename(d)
            if not discovery.is_everything_name(name) and not prompt.confirm(
                    prog, f"'{name}' is empty and its name doesn't contain 'everything'; "
                    "start a new Everything dir here?", args.yes, hint=" (use --yes)"):
                raise new.Refusal("not started")
        suffix, save = _pick_suffix(args, prog, d)
        p = new.plan(d, args.description, args.tags, suffix, year)
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
        path = new.create(p, save, replace_suffix=args.ask_suffix)
    except OSError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1
    print(f"{prog}: created '{p.entry}/' with sidecar '{p.sidecar}'", file=sys.stderr)
    print(path)
    return 0


def _looks_like_everything(d: str) -> str | None:
    """None if `d` looks like an Everything dir, else why it doesn't."""
    if discovery.is_everything_name(os.path.basename(os.path.realpath(d))) \
            or discovery.is_everything_name(os.path.basename(d)):
        return None
    if entries.list_entries(d):
        return None
    return ("its name doesn't contain \"everything\" and it holds no "
            "<yy><seq>[-suffix] entry dirs")


def _current_dir() -> str:
    """The cwd, as $PWD names it when that is the same dir (keeps symlink names)."""
    cwd, pwd = os.getcwd(), os.environ.get("PWD", "")
    return pwd if pwd and os.path.realpath(pwd) == os.path.realpath(cwd) else cwd


def cmd_scan(args: argparse.Namespace) -> int:
    """Search the disk for Everything dirs and add the chosen ones to the saved list."""
    prog = "everything scan"
    home = os.path.expanduser("~")
    if not os.path.isdir(args.root):
        print(f"{prog}: not a dir: {args.root}", file=sys.stderr)
        return 1
    root = os.path.realpath(args.root)
    print(f"{prog}: searching {_tilde(root, home)} ...", file=sys.stderr)
    dirs = discovery.drop_duplicate_links(
        discovery.find_everything_dirs(root, home, network=args.network, links=True))
    if saved_locations.create():
        print(f"{prog}: created {_tilde(saved_locations.list_file(), home)}", file=sys.stderr)
    if not dirs:
        print(f"{prog}: no Everything dirs under {_tilde(root, home)} (try --root <path>)",
              file=sys.stderr)
        return 1
    new_dirs = [d for d in dirs if not saved_locations.contains(d)]
    if not new_dirs:
        print(f"{prog}: found {_plural(len(dirs), 'dir')}, all already saved", file=sys.stderr)
        return 0
    items = [(_tilde(d, home) + ("  (saved)" if d not in new_dirs else ""), d) for d in dirs]
    chosen = [d for d in prompt.choose(prog, items, "add which dirs?", multi=True)
              if d in new_dirs]
    for d in chosen:
        saved_locations.add(d)
    print(f"{prog}: added {_plural(len(chosen), 'dir')} "
          f"({len(new_dirs) - len(chosen)} found but not added)", file=sys.stderr)
    return 0


def cmd_locations(args: argparse.Namespace) -> int:
    """Print the saved Everything dirs, flagging missing ones."""
    prog = "everything locations"
    home = os.path.expanduser("~")
    saved = saved_locations.load()
    if saved is None:
        print(f"{prog}: no list of Everything dirs yet - {saved_locations.HINT}",
              file=sys.stderr)
        return 1
    for d in saved:
        print(_tilde(d, home) + ("" if os.path.isdir(d) else "  (missing)"))
    bookmark = discovery.bookmark_dir()
    if bookmark and not saved_locations.contains(bookmark):
        print(f"{_tilde(bookmark, home)}  (bashmark 'e', searched too, not in the list)")
    if not saved:
        print(f"{prog}: the list is empty", file=sys.stderr)
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    """Add a dir (default: the current one) to the saved list."""
    prog = "everything mark"
    home = os.path.expanduser("~")
    d = os.path.abspath(os.path.expanduser(args.dir)) if args.dir else _current_dir()
    if not os.path.isdir(d):
        print(f"{prog}: not a dir: {args.dir}", file=sys.stderr)
        return 1
    if saved_locations.contains(d):
        saved_locations.create()
        print(f"{prog}: {_tilde(d, home)} is already in the list", file=sys.stderr)
        return 0
    why = _looks_like_everything(d)
    if why and not prompt.confirm(
            prog, f"{_tilde(d, home)} doesn't look like an Everything dir: {why}. Mark it anyway?",
            args.yes, hint=" (use --yes)"):
        print(f"{prog}: not marked", file=sys.stderr)
        return 1
    saved_locations.add(d)
    print(f"{prog}: added {_tilde(d, home)}", file=sys.stderr)
    return 0


def cmd_unmark(args: argparse.Namespace) -> int:
    """Remove one dir from the saved list, after confirming."""
    prog = "everything unmark"
    home = os.path.expanduser("~")
    saved = saved_locations.load()
    if saved is None:
        print(f"{prog}: no list of Everything dirs yet - {saved_locations.HINT}",
              file=sys.stderr)
        return 1
    matches = _matching(saved, args.text)
    if not matches:
        what = f"matching '{args.text}'" if args.text else "saved"
        print(f"{prog}: no Everything dir {what}", file=sys.stderr)
        return 1
    if len(matches) == 1:
        target = matches[0]
    else:
        title = f"several match '{args.text}', remove which?" if args.text else "remove which?"
        picked = prompt.choose(prog, [(_tilde(d, home), d) for d in matches], title)
        if not picked:
            print(f"{prog}: no selection made, refusing", file=sys.stderr)
            return 1
        target = picked[0]
    if not prompt.confirm(prog, f"Remove {_tilde(target, home)} from the list?"):
        print(f"{prog}: not removed", file=sys.stderr)
        return 1
    saved_locations.remove(target)
    print(f"{prog}: removed {_tilde(target, home)}", file=sys.stderr)
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    """Edit the sidecar name of the entry the current dir is in, in $EDITOR."""
    cwd = os.getcwd()
    pwd = os.environ.get("PWD", "")
    if pwd and os.path.realpath(pwd) == os.path.realpath(cwd):
        cwd = pwd  # keeps the name of a symlinked entry dir
    try:
        target = rename.find_target(cwd)
        new_name = rename.ask_name(target)
        if new_name is None:
            print("everything rename: cancelled or unchanged, nothing renamed", file=sys.stderr)
            return 1
        rename.rename(target, new_name)
    except (rename.Refusal, OSError) as e:
        print(f"everything rename: {e}", file=sys.stderr)
        return 1
    print(f"everything rename: '{target.sidecar.name}' -> '{new_name}'", file=sys.stderr)
    return 0


def _render_entry_info(ei: info.EntryInfo, home: str) -> list[str]:
    """Lines summarising an entry for the remove confirmation."""
    s = ei.sidecars[0] if ei.sidecars else None
    newest = datetime.datetime.fromtimestamp(ei.newest).strftime("%Y-%m-%d %H:%M")
    rows = [
        ("Entry", ei.entry.name),
        ("Sidecar", s.name if s else "(none)"),
        ("Description", s.description if s else ""),
        ("Tags", " ".join("@" + t for t in s.tags) if s else ""),
        ("Size", f"{_human_size(ei.size)} ({_plural(ei.files, 'file')}, "
                 f"{_plural(ei.dirs, 'dir')}, {_plural(ei.symlinks, 'symlink')})"),
        ("Last changed", newest),
        ("Location", _tilde(ei.path, home)),
    ]
    lines = [f"  {k + ':':<14}{v}" for k, v in rows]
    lines += [f"  WARNING: {w}" for w in ei.warnings]
    return lines


def cmd_remove(args: argparse.Namespace) -> int:
    """Delete the entry dir you're in plus its sidecar, after a typed confirmation."""
    prog = args.label
    home = os.path.expanduser("~")
    try:
        target = remove.find_entry(_current_dir())
        ei = remove.check_deletable(target)
        if not sys.stdin.isatty():
            raise rename.Refusal("needs a terminal to ask on, refusing - nothing deleted")
        method = remove.choose_method()
        word = remove.confirm_word(ei.sidecars[0].description if ei.sidecars else "",
                                   ei.entry.seq)
        print("\n".join(_render_entry_info(ei, home)), file=sys.stderr)
        print(f"\n  >>> {method.banner} <<<\n", file=sys.stderr)
        print(f"Type {word} (from the description) to delete this entry and its sidecar "
              "(anything else aborts): ",
              end="", file=sys.stderr, flush=True)
        if sys.stdin.readline().strip().casefold() != word.casefold():
            raise rename.Refusal("not confirmed - nothing deleted")
        done = remove.delete(target, method)
    except (rename.Refusal, OSError) as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1
    how = "moved to the trash" if method.trash else "deleted"
    print(f"{prog}: {how}: {', '.join(done)}", file=sys.stderr)
    print(os.path.dirname(target.entry_dir))  # for the shell wrapper to cd into
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Print the summary of the entry you're in, or of one picked with fzf."""
    prog = "everything info"
    home = os.path.expanduser("~")
    try:
        path = remove.find_entry(_current_dir(), for_delete=False).entry_dir
    except remove.NotInEntry:
        path = _pick_entry(args, prog, home)
        if path is None:
            return 1
    except rename.Refusal as e:
        print(f"{prog}: {e.args[0].replace(' - nothing deleted', '')}", file=sys.stderr)
        return 1
    print("\n".join(_render_entry_info(info.entry_info(path), home)))
    return 0


def _pick_entry(args: argparse.Namespace, prog: str, home: str) -> str | None:
    """Let the user pick an entry dir with fzf; None after a message or a cancel."""
    if shutil.which("fzf") is None:
        print(f"{prog}: not inside an entry dir and fzf not found to pick one - cd into an "
              "entry, or install fzf", file=sys.stderr)
        return None
    if args.all:
        dirs = _saved_dirs(prog)
    else:
        bookmark = discovery.bookmark_dir()
        if bookmark is None:
            print(f"{prog}: bashmark 'e' is not set to a valid dir (set it with: s e)",
                  file=sys.stderr)
        dirs = [bookmark] if bookmark else None
    if dirs is None:
        return None
    rows = []
    for d in dirs:
        sidecars = entries.sidecars_by_entry(d)
        for e in entries.list_entries(d):
            s = sidecars.get(e.name, [None])[0]
            label = f"{e.name}  {s.description if s else ''}  " \
                    f"{' '.join('@' + t for t in s.tags) if s else ''}".rstrip()
            if args.all:
                label += f"  [{_tilde(d, home)}]"
            rows.append((e.id, e.name, d, label, os.path.join(d, e.name)))
    if not rows:
        print(f"{prog}: no entries found", file=sys.stderr)
        return None
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    picked = prompt.choose(prog, [(r[3], r[4]) for r in rows], "info for which entry?")
    return picked[0] if picked else None


# Hand-written on purpose (ticket 020); a test fails if a subcommand or an
# Everything wrapper in functions.d/aliases.d is missing here.
# (group title, [(name, one-line description, example), ...])
OVERVIEW = [
    ("`everything` subcommands (everything <command> --help for details)", [
        ("list", "list every Everything dir with entry count, newest entry, suffixes",
         "everything list --paths"),
        ("entries", "list the entries of the Everything dir you're in (-a: all dirs)",
         "everything entries --size"),
        ("pick", "print one Everything dir, via text match or fzf",
         "everything pick archive"),
        ("goto", "print one entry dir, by id or sidecar text",
         "everything goto 1 25"),
        ("stats", "entry, suffix and tag statistics across Everything dirs",
         "everything stats --per-dir"),
        ("check", "report naming problems in Everything dirs (exit 1 if any)",
         "everything check --all-levels"),
        ("new", "create the next entry dir + sidecar and print its path",
         "everything new \"some idea\" ai"),
        ("info", "show size, counts, last change and warnings of one entry (picker outside one)",
         "everything info   everything info -a"),
        ("remove", "delete the entry you're in and its sidecar, after typing a word from its description",
         "everything remove"),
        ("scan", "search the disk for Everything dirs and pick which to save",
         "everything scan --root ~/Main"),
        ("locations", "print the saved Everything dirs", "everything locations"),
        ("mark", "save the current (or given) dir as an Everything dir",
         "everything mark ~/Main/Archive"),
        ("unmark", "remove a dir from the saved list, after asking", "everything unmark archive"),
        ("rename", "edit the sidecar name of the entry you're in, in $EDITOR",
         "everything rename"),
        ("help", "this overview", "everything help"),
    ]),
    ("Shell functions and aliases (functions.d / aliases.d)", [
        ("ge", "cd to an entry of the \"e\" bashmark dir by id or text (-a: all dirs)",
         "ge 1 25   ge fire   ge -a fire"),
        ("gel", "like ge, but searches the current dir", "gel fire"),
        ("cde", "cd to one Everything dir, via text match or fzf", "cde archive"),
        ("lse", "alias for `everything entries`", "lse -a"),
        ("everything", "the command itself; `everything remove` also cds to the parent dir",
         "everything remove"),
        ("mynew", "create the next entry in the current Everything dir and cd into it",
         "mynew \"some idea\" ai"),
    ]),
]


def cmd_help(args: argparse.Namespace) -> int:
    """Print an overview of every Everything-dir command, CLI and shell."""
    width = max(len(name) for _, items in OVERVIEW for name, _, _ in items)
    blocks = []
    for title, items in OVERVIEW:
        lines = [title]
        for name, desc, example in items:
            lines.append(f"  {name:<{width}}  {desc}")
            lines.append(f"  {'':<{width}}    e.g. {example}")
        blocks.append("\n".join(lines))
    print("\n\n".join(blocks))
    return 0


# where `scan` searches when --root isn't given (tests point this at a fake tree)
DEFAULT_ROOT = "/"
NETWORK_HELP = ("also search network filesystems (nfs, cifs, sshfs, ...); local mounts are "
                "always searched")
LIST_NOTE = ("Searches the saved list of Everything dirs plus the \"e\" bashmark dir, each "
             "real dir once (`everything locations` shows it, `scan`/`mark`/`unmark` "
             "change it); never walks the disk.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="everything", description=__doc__,
                                     epilog="`everything help` also lists the shell functions "
                                     "and aliases built on it (ge, cde, ...).")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("list", help="list every saved Everything dir (cde picks one)",
                       description="List the saved Everything dirs (and the \"e\" bashmark "
                       "dir) with their \"<yy><seq>[-suffix]\" entry count, newest entry and "
                       "suffixes. Dirs nested in another listed dir are indented; \"*\" marks "
                       "the \"e\" bashmark used by ge. Symlinked dirs show as "
                       "\"link → target\". Saved dirs that no longer exist are skipped with a "
                       "warning. Fails if no list exists yet (see `everything scan`/`mark`). "
                       "Read-only.")
    p.add_argument("--paths", action="store_true",
                   help="print bare paths only, one per line (for scripting)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("entries", help="list the entries of the Everything dir you're in "
                       "(alias: lse)",
                       description="List the \"<yy><seq>[-suffix]\" entry dirs of the "
                       "Everything dir you're in (the current dir or its nearest ancestor "
                       "whose name contains \"everything\", any case) with the description "
                       "and tags from their \"<entry>_<description>[_@tag...]\" sidecar. "
                       "Sorted by id, then name. Entries without a sidecar get an empty "
                       "description. Read-only.")
    p.add_argument("-a", "--all", action="store_true",
                   help="list the entries of every saved Everything dir, with an "
                        "extra column naming the dir; works from anywhere. " + LIST_NOTE)
    p.add_argument("--size", action="store_true",
                   help="add each entry's recursive size (symlinks not followed) and sort by "
                        "it, largest first")
    p.set_defaults(func=cmd_entries)

    p = sub.add_parser("pick", help="print one Everything dir, via text match or fzf (used by cde)",
                       description="Print the absolute path of one Everything dir. Without "
                       "TEXT, consider all of them; with TEXT, only dirs whose path "
                       "contains it (any case). A single candidate is printed directly, "
                       "several are offered in fzf. Symlinked dirs are printed as the link "
                       "path. " + LIST_NOTE + " Read-only; cde wraps this to cd there.")
    p.add_argument("text", nargs="?", help="case-insensitive substring of the path")
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
                       help="search every saved Everything dir, not just the "
                            "bashmark. " + LIST_NOTE)
    scope.add_argument("--dir", help="search this dir instead of the bashmark (used by gel)")
    p.add_argument("--label", default="everything goto", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_goto)

    p = sub.add_parser("stats", help="entry, suffix and tag statistics across Everything dirs",
                       description="Statistics over the \"<yy><seq>[-suffix]\" entries and "
                       "\"<entry>_<description>[_@tag...]\" sidecars of every Everything dir: "
                       "entry counts (total, distinct ids, per year), entries and id range per "
                       "suffix (split by year, since seq restarts every year) and tag usage. "
                       "One combined total by default. Read-only; problems such as missing "
                       "sidecars are left to the checker. " + LIST_NOTE)
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
                       "is shown. Read-only. " + LIST_NOTE)
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
                       "new year. In an empty dir it creates the first entry (after asking, if "
                       "the dir name has no \"everything\" in it; without a terminal that "
                       "needs --yes); a dir with other files but no entries is refused. The "
                       "suffix is the one in the dir's \".mynew-suffix\", else the content of "
                       "~/.devbox_id, else it is asked once; the one used is saved in "
                       "\".mynew-suffix\". Never overwrites an entry or sidecar. mynew wraps "
                       "this to cd there.")
    p.add_argument("description", help="free text, turned into lower_snake_case")
    p.add_argument("tags", nargs="*", metavar="tag", help="tags, with or without a leading @")
    p.add_argument("--dir", default=".", help="the Everything dir (default: current dir)")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--suffix", metavar="S",
                       help="use suffix S for this entry only; nothing is saved")
    which.add_argument("--ask-suffix", action="store_true",
                       help="ask for a suffix (needs a terminal) and save it to "
                            ".mynew-suffix, replacing the old one")
    p.add_argument("--yes", action="store_true",
                   help="start a new Everything dir in an empty dir whose name has no "
                        "\"everything\" in it, without asking")
    p.add_argument("--label", default="everything new", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("info", help="show read-only stats for one entry",
                       description="Print what the entry you're in holds: id, sidecar, "
                       "description and tags, size, file/dir/symlink counts, last change, and "
                       "warnings (git repos with uncommitted or unpushed work, symlinks) - the "
                       "same summary `everything remove` shows, without any delete prompt. "
                       "Outside an entry, pick one with fzf from the \"e\" bashmark dir (-a: "
                       "from every saved Everything dir). Read-only. Not `stats`, which "
                       "aggregates across dirs.")
    p.add_argument("-a", "--all", action="store_true",
                   help="outside an entry, pick from every saved Everything dir. " + LIST_NOTE)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("remove", help="delete the entry you're in and its sidecar",
                       description="Run inside an entry dir (or below it). Shows what the "
                       "entry holds - id, sidecar, size, file count, last change, and warnings "
                       "for git repos with uncommitted or unpushed work and for symlinks - "
                       "then deletes the entry dir AND its sidecar, but only after you type "
                       "the first word of its description (the first 6 characters if that word is "
                       "shorter than 3 letters or digits), in any case. Files go to the trash (gio trash "
                       "or trash-put) if one is installed, else they are deleted for good; "
                       "the prompt says which. Refuses outside an entry, without exactly one "
                       "sidecar, for a symlink or mount point, and without a terminal. Prints "
                       "the parent dir on success; the `everything` shell function cds there.")
    p.add_argument("--label", default="everything remove", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("scan", help="search the disk for Everything dirs and save the chosen ones",
                       description="The only command that walks the disk: find every dir "
                       "whose name contains \"everything\" (any case) and ask which to add "
                       "to the saved list (fzf multi-select if available, else a numbered "
                       "prompt; dirs already saved are marked). Creates the list file if "
                       "missing. The other commands only read the list.")
    p.add_argument("--root", default=DEFAULT_ROOT, help="where to search (default: /)")
    p.add_argument("--network", action="store_true", help=NETWORK_HELP)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("locations", help="print the saved Everything dirs",
                       description="Print the saved Everything dirs, one per line; ones that "
                       "no longer exist are flagged, and so is the \"e\" bashmark dir if it "
                       "isn't in the list (it is searched anyway). Read-only.")
    p.set_defaults(func=cmd_locations)

    p = sub.add_parser("mark", help="save the current (or given) dir as an Everything dir",
                       description="Add DIR (default: the current dir) to the saved list, "
                       "creating the list if needed. A dir that doesn't look Everything-like "
                       "(no \"everything\" in its name and no <yy><seq>[-suffix] entries) "
                       "is only added after confirming; without a terminal that is refused "
                       "unless --yes. A marked dir counts as an Everything dir (also for "
                       "`lse`); nested dirs need their own mark.")
    p.add_argument("dir", nargs="?", help="the dir to mark (default: current dir)")
    p.add_argument("--yes", action="store_true",
                   help="don't ask about a dir that doesn't look Everything-like")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("unmark", help="remove a dir from the saved list, after asking",
                       description="Remove one dir from the saved list. TEXT matches the saved "
                       "paths (case-insensitive substring; without it all are candidates); "
                       "several matches are offered in fzf or a numbered prompt. Always asks "
                       "before removing, so it refuses without a terminal.")
    p.add_argument("text", nargs="?", help="case-insensitive substring of the saved path")
    p.set_defaults(func=cmd_unmark)

    p = sub.add_parser("rename", help="edit the sidecar name of the entry you're in, in $EDITOR",
                       description="Run inside an entry dir (or below it): open $EDITOR "
                       "(default vim) on the part of the sidecar name after the entry id "
                       "and before the extension, e.g. \"working_on_dotfiles_@ai\", and "
                       "rename the sidecar to the edited text. The name is checked first "
                       "(letters, digits, _ and _@tags, at most 80 characters, no existing "
                       "file); on a problem the editor reopens with the error shown. An "
                       "editor error (:cq), an empty line or no change renames nothing and "
                       "exits 1.")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("help", help="overview of every Everything-dir command, incl. shell "
                       "wrappers", description="Print a short overview of the `everything` "
                       "subcommands and the shell functions/aliases built on them, each "
                       "with an example.")
    p.set_defaults(func=cmd_help)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:  # e.g. `everything list --paths | head -1`
        sys.stderr.close()
        return 0
