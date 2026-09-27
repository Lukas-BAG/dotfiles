"""`everything` command: subcommands over all Everything dirs."""

import argparse
import os
import shutil
import subprocess
import sys

from . import discovery, entries


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
    dirs, age = discovery.find_everything_dirs_cached(root, home, fresh=args.fresh)
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


def cmd_pick(args: argparse.Namespace) -> int:
    """Print one Everything dir, matched by text and/or picked with fzf."""
    prog = "everything pick"
    found = _discover(args, prog)
    if found is None:
        return 1
    _, dirs = found

    needle = args.text.casefold() if args.text else None
    matches = [d for d in dirs if needle is None or needle in d.casefold()]
    if not matches:
        print(f"{prog}: no Everything dir matching '{args.text}'", file=sys.stderr)
        return 1
    if len(matches) == 1:
        what = f"matching '{args.text}'" if needle else "found"
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
    prompt = f"multiple matches for '{args.text}' > " if needle else "Everything dir > "
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="everything", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("list", help="list every Everything dir (alias: lse)",
                       description="List every Everything or Everything-like dir (name "
                       "contains \"everything\", any case) with its \"<yy><seq>[-suffix]\" "
                       "entry count, newest entry and suffixes. Dirs nested in another "
                       "listed dir are indented; \"*\" marks the \"e\" bashmark used by ge. "
                       "Read-only.")
    p.add_argument("--root", default=os.path.expanduser("~"),
                   help="where to search (default: $HOME)")
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
    p.add_argument("--root", default=os.path.expanduser("~"),
                   help="where to search (default: $HOME)")
    p.add_argument("--fresh", action="store_true",
                   help="ignore the cached Everything-dir list (up to 6 h old) and search again "
                        "(no-op while the cache is disabled)")
    p.set_defaults(func=cmd_pick)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:  # e.g. `everything list --paths | head -1`
        sys.stderr.close()
        return 0
