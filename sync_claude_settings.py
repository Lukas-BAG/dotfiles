#!/usr/bin/env python3
"""Sync the tracked Claude Code settings into ~/.claude/settings.json.

~/.claude/settings.json is a real, per-machine file that Claude Code writes
to during normal use, so it isn't stowed. Instead this repo tracks the
settings that should hold on every machine, in two files:

  claude_settings/defaults.json  -- set only where the live file lacks the key,
                                    so a per-machine choice (e.g. model) survives
  claude_settings/enforced.json  -- must hold everywhere; missing keys are
                                    added, differing values are shown as a diff
                                    and applied only after a y/n confirmation

Merge rules (both files):
  - JSON objects are walked key by key; keys the tracked files don't mention
    are left alone, at any depth.
  - Anything else (lists, strings, numbers, booleans, null) is one value.
    A list is never combined with the live list, only compared as a whole.
  - A key path may appear in only one of the two files.
  - Keys dropped from the tracked files are not removed from the live file;
    remove them by hand.

Run from anywhere; `init_or_deinit_stow.py` also calls it after stowing when
the `ai` module is selected. Use --dry-run to see what would change.
"""
import argparse
import copy
import difflib
import json
import os
import sys
import tempfile
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
TRACKED_DIR = os.path.join(REPO_ROOT, "claude_settings")
DEFAULTS_FILE = "defaults.json"
ENFORCED_FILE = "enforced.json"
BACKUPS_DIR = os.path.join(REPO_ROOT, "backups")
BACKUP_NAME = "claude-settings.json"
LIVE_SETTINGS = "~/.claude/settings.json"


class SyncError(Exception):
    pass


def json_equal(a, b):
    """Equality as JSON sees it: `true` is not `1`, and `1` is not `"1"`.

    Python's == treats True == 1 == 1.0, which would hide a real change.
    Ints and floats still compare by value (JSON has one number type).
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    return a == b


def format_path(path):
    return ".".join(path)


def leaf_paths(obj, prefix=()):
    """Key paths of every non-object value (and empty object) in `obj`."""
    if isinstance(obj, dict) and obj:
        for key, value in obj.items():
            yield from leaf_paths(value, prefix + (key,))
    elif prefix:
        yield prefix


def check_no_overlap(defaults, enforced):
    """A key path set by both files would make the result depend on order."""
    enforced_paths = list(leaf_paths(enforced))
    overlaps = []
    for d in leaf_paths(defaults):
        for e in enforced_paths:
            shorter = min(len(d), len(e))
            if d[:shorter] == e[:shorter]:
                overlaps.append((d, e))
    if overlaps:
        lines = [f"  defaults: {format_path(d)}  /  enforced: {format_path(e)}"
                 for d, e in overlaps]
        raise SyncError("defaults.json and enforced.json both set these keys:\n"
                        + "\n".join(lines))


def apply_defaults(live, defaults, prefix=()):
    """Set keys from `defaults` that `live` lacks. Never overwrites anything.

    Mutates `live`; returns the (path, value) pairs that were added.
    """
    added = []
    for key, value in defaults.items():
        path = prefix + (key,)
        if key not in live:
            live[key] = copy.deepcopy(value)
            added.append((path, value))
        elif isinstance(value, dict) and isinstance(live[key], dict):
            added.extend(apply_defaults(live[key], value, path))
        # else: the live file already has its own value here -- keep it,
        # even if its type differs from the default.
    return added


def apply_enforced(live, enforced, prefix=()):
    """Add enforced keys `live` lacks; collect the ones where it differs.

    Mutates `live` for the missing keys only. Returns (added, conflicts):
    added is a list of (path, value), conflicts a list of
    (path, live_value, enforced_value). An enforced object meeting a live
    non-object (e.g. `hooks` is a string locally) is one conflict on the
    whole object -- it is never overwritten without asking.
    """
    added, conflicts = [], []
    for key, value in enforced.items():
        path = prefix + (key,)
        if key not in live:
            live[key] = copy.deepcopy(value)
            added.append((path, value))
        elif isinstance(value, dict) and isinstance(live[key], dict):
            sub_added, sub_conflicts = apply_enforced(live[key], value, path)
            added.extend(sub_added)
            conflicts.extend(sub_conflicts)
        elif not json_equal(live[key], value):
            conflicts.append((path, copy.deepcopy(live[key]), value))
    return added, conflicts


def set_path(obj, path, value):
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = copy.deepcopy(value)


def dump(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False)


def conflict_diff(path, old, new):
    name = format_path(path)
    lines = difflib.unified_diff(
        dump(old).splitlines(), dump(new).splitlines(),
        fromfile=f"{name} (this machine)", tofile=f"{name} (enforced)", lineterm="")
    return "\n".join(lines)


def load_json_object(path, what):
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise SyncError(f"{what} is not valid JSON: {path}: {e}")
    if not isinstance(data, dict):
        raise SyncError(f"{what} must contain a JSON object: {path}")
    return data


def find_symlink(path):
    """The first symlink among `path` and its parents below $HOME, or None.

    A symlinked settings.json (or a folded ~/.claude) means the file lives
    in some repo; writing there would put this machine's state into git.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    current = os.path.abspath(path)
    while True:
        if os.path.islink(current):
            return current
        parent = os.path.dirname(current)
        if parent == current or os.path.realpath(parent) == home:
            return None
        current = parent


def backup(live_path, backups_dir):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(backups_dir, timestamp)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, BACKUP_NAME)
    with open(live_path, "rb") as src, open(dest, "wb") as dst:
        dst.write(src.read())
    return dest


def write_atomically(path, data):
    """Write via a temp file in the same dir + rename, so Claude Code (which
    reloads this file while running) never sees a half-written file."""
    directory = os.path.dirname(path)
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else None
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".settings.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(dump(data) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def ask(prompt, input_fn):
    while True:
        answer = input_fn(prompt).strip().lower()
        if answer in ("y", "n", "a"):
            return answer
        print("Please answer y, n or a.")


def resolve_conflicts(merged, conflicts, input_fn):
    """Ask about each conflict; apply the accepted ones to `merged`."""
    applied, declined = [], []
    apply_all = False
    for i, (path, old, new) in enumerate(conflicts, 1):
        if not apply_all:
            print(f"\nConflict {i}/{len(conflicts)}: {format_path(path)}")
            print(conflict_diff(path, old, new))
            answer = ask("Apply the enforced value? [y]es / [n]o / [a]ll remaining: ", input_fn)
            if answer == "a":
                apply_all = True
            elif answer == "n":
                declined.append(path)
                continue
        set_path(merged, path, new)
        applied.append(path)
    return applied, declined


def print_changes(title, changes):
    if changes:
        print(title)
        for path, value in changes:
            print(f"  {format_path(path)} = {json.dumps(value, ensure_ascii=False)}")


def sync(live_path, tracked_dir=TRACKED_DIR, backups_dir=BACKUPS_DIR,
         dry_run=False, interactive=None, input_fn=input):
    """Run one sync. Returns 0 on success, raises SyncError on refusal."""
    if interactive is None:
        interactive = sys.stdin.isatty()

    defaults = load_json_object(os.path.join(tracked_dir, DEFAULTS_FILE), "Tracked defaults")
    enforced = load_json_object(os.path.join(tracked_dir, ENFORCED_FILE), "Tracked enforced settings")
    check_no_overlap(defaults, enforced)

    link = find_symlink(live_path)
    if link:
        raise SyncError(
            f"{link} is a symlink (-> {os.path.realpath(link)}). Refusing to write "
            f"through it. Replace it with a real file first, e.g. copy the target's "
            f"content to {live_path}, then run this again.")

    exists = os.path.exists(live_path)
    if exists:
        original = load_json_object(live_path, "Live settings")
    else:
        original = {}
        print(f"{live_path} does not exist; it will be created from the tracked "
              f"defaults and enforced settings.")

    merged = copy.deepcopy(original)
    added_defaults = apply_defaults(merged, defaults)
    added_enforced, conflicts = apply_enforced(merged, enforced)

    print_changes("Defaults to add (key was missing):", added_defaults)
    print_changes("Enforced settings to add (key was missing):", added_enforced)

    if dry_run:
        for path, old, new in conflicts:
            print(f"\nConflict (would ask): {format_path(path)}")
            print(conflict_diff(path, old, new))
        if not (added_defaults or added_enforced or conflicts):
            print("Already in sync, nothing to do.")
        print("\nDry run: nothing written.")
        return 0

    declined = []
    if conflicts:
        if interactive:
            _, declined = resolve_conflicts(merged, conflicts, input_fn)
        else:
            declined = [path for path, _, _ in conflicts]
            print("\nNo terminal to ask on; left these enforced settings as they are "
                  "on this machine:")
            for path, old, new in conflicts:
                print(conflict_diff(path, old, new))

    if exists and json_equal(merged, original):
        print("Already in sync, nothing written." if not declined
              else "Nothing written.")
    else:
        if exists:
            print(f"Backed up {live_path} -> {backup(live_path, backups_dir)}")
        else:
            os.makedirs(os.path.dirname(live_path), exist_ok=True)
        write_atomically(live_path, merged)
        print(f"Wrote {live_path}")

    if declined:
        print("Declined (will be asked again next run): "
              + ", ".join(format_path(p) for p in declined))
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Merge the tracked Claude Code settings (claude_settings/) into "
                    "the live ~/.claude/settings.json.")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would change, including conflicts, without writing")
    parser.add_argument("--settings", default=LIVE_SETTINGS,
                        help=f"live settings file to update (default: {LIVE_SETTINGS})")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        return sync(os.path.expanduser(args.settings), dry_run=args.dry_run)
    except SyncError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nAborted, nothing written.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
