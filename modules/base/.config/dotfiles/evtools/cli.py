"""`everything` command: subcommands over all Everything dirs."""

import argparse
import os
import sys

from . import discovery, entries


def _tilde(path: str, home: str) -> str:
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def cmd_list(args: argparse.Namespace) -> int:
    """List every Everything dir with entry count, newest entry and suffixes."""
    home = os.path.expanduser("~")
    if not os.path.isdir(args.root):
        print(f"everything list: not a dir: {args.root}", file=sys.stderr)
        return 1
    root = os.path.realpath(args.root)
    dirs = discovery.find_everything_dirs(root, home)

    if not dirs:
        print(f"everything list: no Everything dirs under {_tilde(root, home)} (try --root <path>)",
              file=sys.stderr)
        return 1
    if args.paths:
        print("\n".join(dirs))
        return 0

    bookmark = discovery.bookmark_dir()
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
    print(f"Everything dirs under {_tilde(root, home)} ({len(dirs)} found, {nested} nested)")
    print()
    print(f"  {'PATH':<{width - 2}}  {'ENTRIES':>7}  {'NEWEST':<12}  SUFFIX")
    for display, count, newest, suffixes in rows:
        print(f"{display:<{width}}  {count:>7}  {newest:<12}  {suffixes}")
    if bookmark in dirs:
        print()
        print("* = bashmark 'e' (used by ge)")
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
    p.add_argument("--paths", action="store_true",
                   help="print bare absolute paths only, one per line (for scripting)")
    p.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:  # e.g. `everything list --paths | head -1`
        sys.stderr.close()
        return 0
