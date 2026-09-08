#!/usr/bin/env python3
"""Guided interactive setup for dotfilesv3.

Walks the user through selecting modules, writes .module_list, detects any
stow conflicts via `stow --simulate`, offers to back up or delete exactly the
conflicting paths stow itself reports, then runs the real stow setup.

Safety invariant: the only paths ever passed to a move/delete operation are
the exact paths parsed out of stow's own --simulate output. Nothing is ever
re-derived by walking directories, globbed, or applied to a directory.
"""
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

MODULE_LIST_FILE = ".module_list"
MODULE_LIST_TEMPLATE = ".module_list_template"
MODULES_DIR = "modules"
SYS_MODULES_DIR = "sys_modules"
BACKUPS_DIR = "backups"
GITIGNORE_FILE = ".gitignore"
STOW_HELPER = "init_or_deinit_stow.py"
INIT_SCRIPTS_DIR = os.path.join("Scripts", "Initialization_and_Saving_State_Scripts")
APT_INSTALL_SCRIPT = os.path.join(INIT_SCRIPTS_DIR, "apt_install_programs.sh")
BASHMARKS_SCRIPT = os.path.join(INIT_SCRIPTS_DIR, "init_bashmarks.sh")

# Matches stow's conflict lines, e.g.:
#   "  * existing target is neither a link nor a directory: .bashrc"
#   "  * existing target is not owned by stow: .config/foo"
CONFLICT_LINE_RE = re.compile(r"^\s*\*\s.*:\s*(.+?)\s*$")


def discover_modules(directory):
    if not os.path.isdir(directory):
        return []
    return sorted(
        name for name in os.listdir(directory)
        if os.path.isdir(os.path.join(directory, name))
    )


def parse_module_defaults():
    """Active (uncommented) entries in .module_list if it exists, else
    .module_list_template, e.g. {"base", "sys/services"}."""
    source = MODULE_LIST_FILE if os.path.isfile(MODULE_LIST_FILE) else MODULE_LIST_TEMPLATE
    defaults = set()
    if os.path.isfile(source):
        with open(source) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    defaults.add(line)
    return defaults


def checkbox_prompt(entries, checked):
    """entries: list of (key, label, is_section_header) in display order.
    checked: set of keys pre-checked. Returns the resulting set of checked keys."""
    selectable = [e for e in entries if not e[2]]
    state = {key: (key in checked) for key, _label, _hdr in selectable}
    index_of = {i + 1: key for i, (key, _label, _hdr) in enumerate(selectable)}

    while True:
        print()
        i = 0
        for key, label, is_header in entries:
            if is_header:
                print(f"-- {label} --")
                continue
            i += 1
            mark = "x" if state[key] else " "
            print(f"  {i:2d}. [{mark}] {label}")
        print()
        print("Type numbers separated by spaces to toggle, then Enter on an empty")
        print("line to confirm. Commands: 'all', 'none'.")
        raw = input("> ").strip()

        if raw == "":
            break
        if raw == "all":
            for key in state:
                state[key] = True
            continue
        if raw == "none":
            for key in state:
                state[key] = False
            continue

        for tok in raw.split():
            if tok.isdigit() and int(tok) in index_of:
                key = index_of[int(tok)]
                state[key] = not state[key]
            else:
                print(f"Ignoring invalid entry: {tok!r}")

    return {key for key, value in state.items() if value}


def run_stow_simulate(target, directory, modules, sudo=False):
    """Return the list of conflicting paths (relative to `target`) that stow's
    own --simulate run reports. Empty list means no conflicts."""
    if not modules:
        return []
    cmd = ["stow", "--simulate", "-t", target, "-d", directory] + modules
    if sudo:
        cmd.insert(0, "sudo")
    result = subprocess.run(cmd, capture_output=True, text=True)
    conflicts = []
    for line in result.stderr.splitlines():
        match = CONFLICT_LINE_RE.match(line)
        if match:
            conflicts.append(match.group(1))
    return conflicts


def build_conflict_records(rel_paths, target_root, sudo):
    """Turn stow-reported relative paths into absolute-path records, with a
    sanity check that each resolved path is actually inside target_root."""
    records = []
    target_root_abs = os.path.realpath(target_root)
    for rel_path in rel_paths:
        abs_path = os.path.normpath(os.path.join(target_root, rel_path))
        real_abs_path = os.path.realpath(os.path.dirname(abs_path))
        if not (real_abs_path == target_root_abs or real_abs_path.startswith(target_root_abs + os.sep)):
            print(f"Refusing to proceed: stow-reported path resolves outside its target root: {abs_path}")
            sys.exit(1)
        if os.path.isdir(abs_path) and not os.path.islink(abs_path):
            print(f"Refusing to proceed: stow reported a directory as a conflict, which should never happen: {abs_path}")
            sys.exit(1)
        records.append({"rel_path": rel_path, "abs_path": abs_path, "sudo": sudo})
    return records


def collect_conflicts(home_modules, sys_modules):
    home_conflicts = build_conflict_records(
        run_stow_simulate(os.environ["HOME"], MODULES_DIR, home_modules),
        os.environ["HOME"],
        sudo=False,
    )
    sys_conflicts = build_conflict_records(
        run_stow_simulate("/", SYS_MODULES_DIR, sys_modules, sudo=True),
        "/",
        sudo=True,
    )
    return home_conflicts + sys_conflicts


def backup_conflicts(conflicts):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_root = os.path.join(BACKUPS_DIR, timestamp)
    for record in conflicts:
        dest = os.path.join(dest_root, record["rel_path"])
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        if record["sudo"]:
            subprocess.run(["sudo", "mv", "--", record["abs_path"], dest], check=True)
        else:
            shutil.move(record["abs_path"], dest)
        print(f"Moved {record['abs_path']} -> {dest}")
    print(f"\nBackup complete: {dest_root}")


def delete_conflicts(conflicts):
    for record in conflicts:
        if record["sudo"]:
            subprocess.run(["sudo", "rm", "-f", "--", record["abs_path"]], check=True)
        else:
            os.remove(record["abs_path"])
        print(f"Deleted {record['abs_path']}")


def ensure_backups_gitignored():
    entry = f"{BACKUPS_DIR}/"
    lines = []
    if os.path.isfile(GITIGNORE_FILE):
        with open(GITIGNORE_FILE) as f:
            lines = f.read().splitlines()
    if entry not in lines and BACKUPS_DIR not in lines:
        with open(GITIGNORE_FILE, "a") as f:
            if lines and not lines[-1] == "":
                f.write("\n")
            f.write(f"{entry}\n")


def resolve_conflicts_interactively(conflicts):
    print("\nThe following pre-existing paths conflict with the selected modules:")
    for record in conflicts:
        print(f"  {record['abs_path']}")

    print()
    print("How should these be handled?")
    print("  1. Move to backup (dotfilesv3/backups/<timestamp>/) [default]")
    print("  2. Delete")
    choice = input("> ").strip()

    if choice == "2":
        print("\nThe following paths will be PERMANENTLY DELETED:")
        for record in conflicts:
            print(f"  {record['abs_path']}")
        confirm = input("\nType DELETE to confirm: ").strip()
        if confirm != "DELETE":
            print("Confirmation not given, aborting.")
            sys.exit(1)
        delete_conflicts(conflicts)
    else:
        backup_conflicts(conflicts)


def install_packages(mode):
    subprocess.run(["bash", APT_INSTALL_SCRIPT, mode], check=True)


def update_submodules():
    subprocess.run(["git", "submodule", "update", "--init", "--recursive"], check=True)


def set_default_bashmarks():
    subprocess.run(["bash", "-c", f"source {BASHMARKS_SCRIPT}"], check=True)


def top_level_prompt():
    entries = [
        ("essential", "install necessary packages (git, stow)", False),
        ("additional", "install additional packages (the rest)", False),
        ("submodules", "update git submodules (git submodule update --init --recursive)", False),
        ("bashmarks", "set default bashmarks", False),
        ("modules", "customize modules", False),
    ]
    defaults = {"essential", "submodules", "bashmarks", "modules"}

    print("dotfilesv3 interactive setup")
    print("============================")
    print("Select which setup steps to run.")

    return checkbox_prompt(entries, checked=defaults)


def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(repo_root)

    steps = top_level_prompt()

    if "essential" in steps:
        print("\nInstalling necessary packages (git, stow)...")
        install_packages("essential")
    if "additional" in steps:
        print("\nInstalling additional packages...")
        install_packages("additional")
    if "submodules" in steps:
        print("\nUpdating git submodules...")
        update_submodules()
    if "bashmarks" in steps:
        print("\nSetting default bashmarks...")
        set_default_bashmarks()

    if "modules" not in steps:
        print("\nSkipping module customization.")
        return

    home_module_names = discover_modules(MODULES_DIR)
    sys_module_names = discover_modules(SYS_MODULES_DIR)
    if not home_module_names and not sys_module_names:
        print("No modules found under modules/ or sys_modules/, nothing to do.")
        sys.exit(0)

    defaults = parse_module_defaults()
    defaults_source = MODULE_LIST_FILE if os.path.isfile(MODULE_LIST_FILE) else MODULE_LIST_TEMPLATE

    entries = [(name, name, False) for name in home_module_names]
    if sys_module_names:
        entries.append((None, "sys_modules (require sudo)", True))
        entries += [(f"sys/{name}", f"sys/{name}", False) for name in sys_module_names]

    print("\nSelect which modules to activate on this machine.")
    print(f"(defaults pre-filled from {defaults_source})")

    selected = checkbox_prompt(entries, checked=defaults)

    home_selected = sorted(name for name in selected if not name.startswith("sys/"))
    sys_selected = sorted(name[len("sys/"):] for name in selected if name.startswith("sys/"))

    print("\nRunning a dry run (stow --simulate) to check for conflicts...")
    conflicts = collect_conflicts(home_selected, sys_selected)

    if conflicts:
        resolve_conflicts_interactively(conflicts)
        ensure_backups_gitignored()

        print("\nRe-checking for conflicts after resolution...")
        remaining = collect_conflicts(home_selected, sys_selected)
        if remaining:
            print("Conflicts still remain, aborting without writing .module_list:")
            for record in remaining:
                print(f"  {record['abs_path']}")
            sys.exit(1)
    else:
        print("No conflicts found.")

    with open(MODULE_LIST_FILE, "w") as f:
        f.write("# Generated by interactive_setup.py\n")
        for name in home_selected:
            f.write(f"{name}\n")
        for name in sys_selected:
            f.write(f"sys/{name}\n")
    print(f"\nWrote {MODULE_LIST_FILE}.")

    print("\nRunning the real stow setup...")
    subprocess.run([sys.executable, STOW_HELPER], check=True, cwd=repo_root)


if __name__ == "__main__":
    main()
