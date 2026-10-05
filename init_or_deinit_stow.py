#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import argparse

MODULE_LIST_FILE = ".module_list"
MODULES_DIR = "modules"
SYS_MODULES_DIR = "sys_modules"
CLAUDE_SETTINGS_SYNC = "sync_claude_settings.py"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Script for initializing or deinitializing stow dotfiles"
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-D", "--deinit",
        action="store_true",
        help="Unstow the dotfiles (remove all links)"
    )
    group.add_argument(
        "-R", "--restow",
        action="store_true",
        help="Unstow everything then stow again (useful after moving files)"
    )

    return parser.parse_args()

PERSISTENT_FILES = [
    ("~/.config/dotfiles/system_local", "bashrc.sh"),
    ("~/.config/dotfiles/system_local", "i3_config_addon"),
    ("~/.config/dotfiles/system_local", "gitconfig"),
    ("~/.config/dotfiles/system_local", "monitors.sh"),
]

# Directories that must never become whole-directory stow symlinks, keyed by
# the module that owns files inside them. Without this, stow will happily
# replace the target with a symlink into the module the first time it's
# stowed and the target doesn't exist yet -- but for a directory like
# ~/.claude, other (non-dotfiles) tools write their own runtime state into
# it (session data, caches, etc.), and that state would then end up
# physically inside the git-tracked module dir. Pre-creating the directory
# as a real dir forces stow to fold: it links only the individual files the
# module actually owns and leaves the rest of the directory alone. (The ai
# module currently owns no files in ~/.claude -- settings.json is merged in
# by sync_claude_settings.py instead -- but anything added there later must
# not fold the whole dir.)
#
# Same for ~/.config/systemd/user (`systemctl --user enable/edit` writes
# wants-links and drop-in overrides there) and ~/.local/bin (pip/pipx,
# installers). Stow folds at the highest missing directory, so listing the
# deepest dir is enough: its parents get pre-created too.
#
# ~/.config/dotfiles/aliases.d is shared by the base and ai modules, so it
# must be a real dir with one link per file, never a fold into either module.
NON_FOLDING_DIRS = {
    "ai": ["~/.claude", "~/.local/bin", "~/.config/dotfiles/aliases.d"],
    "base": ["~/.local/bin", "~/.config/dotfiles/aliases.d"],
    "i3": [
        "~/.local/bin",
        "~/.local/share/applications",
        "~/.config/dotfiles/aliases.d",
    ],
    "services": [
        "~/.config/systemd/user/timers.target.wants",
        "~/.local/bin",
    ],
}

# Symlinks an earlier layout of the modules left in $HOME. Removed before
# stowing, but only if they point into a module dir at something that no
# longer exists (so the links the current layout creates at the same paths,
# e.g. after files moved from base to i3, are never touched).
STALE_LINKS = [
    # ticket 030: ai aliases moved to ~/.config/dotfiles
    "~/.config/bash_dotfiles",
    # ticket 032: desktop files moved from base to i3
    "~/.Xresources",
    "~/.xbindkeysrc",
    "~/.config/xkb",
    "~/.config/dunst",
    "~/.config/sway",
    "~/.config/dotfiles/scripts",
    "~/Main/Scripts/Startup_Script",
    "~/Main/Scripts/Helper_Scripts",
    "~/Main/Data",
    "~/.local/bin/myScreenshot",
    "~/.local/share/applications/startup_script_with_programs.desktop",
]


BASHRC_BLOCK = """\
[ -f ~/.config/dotfiles/bashrc.sh ] && . ~/.config/dotfiles/bashrc.sh
"""
PROFILE_BLOCK = """\
[ -f ~/.config/dotfiles/profile.sh ] && . ~/.config/dotfiles/profile.sh
"""
BLOCK_NAME = "dotfiles"
BACKUP_SUFFIX = ".pre-dotfiles"


class ManagedBlockError(Exception):
    pass


def block_markers(name=BLOCK_NAME):
    return f"# >>> {name} >>>", f"# <<< {name} <<<"


def add_managed_block(path, block, name=BLOCK_NAME):
    """Append a marked block to the end of `path`; return what was done.

    Idempotent. A missing file is created containing only the block (never
    seeded from the distro skel); an existing file is backed up once to
    `path` + BACKUP_SUFFIX and its lines are never modified. Refuses to
    write through a symlink.
    """
    begin, end = block_markers(name)
    if os.path.islink(path):
        raise ManagedBlockError(
            f"{path} is a symlink (probably a stow link into this repo); "
            f"not writing through it. Migrate by hand:\n"
            f"  target=$(readlink -f {path})\n"
            f"  rm {path}\n"
            f"  cp \"$target\" {path}\n"
            f"then run the init script again.")
    if os.path.exists(path):
        with open(path) as f:
            content = f.read()
        if begin in content:
            return "present"
        backup = path + BACKUP_SUFFIX
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
    else:
        content = ""
    if content and not content.endswith("\n"):
        content += "\n"
    if content:
        content += "\n"
    content += f"{begin}\n{block}{end}\n"
    with open(path, "w") as f:
        f.write(content)
    return "added"


def remove_managed_block(path, name=BLOCK_NAME):
    """Remove only the marked block (and the blank line before it)."""
    begin, end = block_markers(name)
    if os.path.islink(path) or not os.path.isfile(path):
        return "absent"
    with open(path) as f:
        lines = f.read().split("\n")
    try:
        i = lines.index(begin)
        j = lines.index(end, i)
    except ValueError:
        return "absent"
    if i > 0 and lines[i - 1] == "":
        i -= 1
    del lines[i:j + 1]
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return "removed"


class StowHelper:
    def __init__(self):
        self.args = parse_args()

    def ensure_persistent_files(self):
        for dir_path, filename in PERSISTENT_FILES:
            expanded = os.path.expanduser(dir_path)
            os.makedirs(expanded, exist_ok=True)
            filepath = os.path.join(expanded, filename)
            if not os.path.exists(filepath):
                open(filepath, "a").close()
                print(f"Created persistent file: {filepath}")

    def manage_bashrc(self, deinit):
        path = os.path.expanduser("~/.bashrc")
        if deinit:
            if remove_managed_block(path) == "removed":
                print(f"Removed the dotfiles block from {path}")
            return
        if os.path.exists(os.path.expanduser("~/.bash_profile")):
            print("Warning: ~/.bash_profile exists, so login bash shells ignore "
                  "~/.profile and may never read ~/.bashrc.")
        try:
            result = add_managed_block(path, BASHRC_BLOCK)
        except ManagedBlockError as e:
            print(f"Error: {e}")
            return
        if result == "added":
            print(f"Added the dotfiles block to {path}")

    def manage_profile(self, deinit):
        path = os.path.expanduser("~/.profile")
        if deinit:
            if remove_managed_block(path) == "removed":
                print(f"Removed the dotfiles block from {path}")
            return
        try:
            result = add_managed_block(path, PROFILE_BLOCK)
        except ManagedBlockError as e:
            print(f"Error: {e}")
            return
        if result == "added":
            print(f"Added the dotfiles block to {path}")

    def unfold_existing(self, path, home_modules):
        """Remove a stow fold at `path` or one of its parents below $HOME.

        A fold is a directory symlink into one of the selected modules, left
        behind by an earlier stow run (before the dir was in NON_FOLDING_DIRS).
        Unlinking it loses nothing: the files live in the module and get
        linked back one by one by the stow run that follows.
        """
        home = os.path.realpath(os.path.expanduser("~"))
        module_roots = [os.path.realpath(os.path.join(MODULES_DIR, m)) for m in home_modules]
        current = path
        while True:
            parent = os.path.dirname(current)
            if os.path.islink(current):
                target = os.path.realpath(current)
                if any(os.path.commonpath([target, root]) == root for root in module_roots):
                    os.unlink(current)
                    print(f"Unfolded {current} (was a stow symlink to {target})")
                return
            if parent == current or os.path.realpath(parent) == home:
                return
            current = parent

    def remove_stale_links(self, home_modules):
        module_roots = [os.path.realpath(os.path.join(MODULES_DIR, m)) for m in os.listdir(MODULES_DIR)]
        for link in STALE_LINKS:
            expanded = os.path.expanduser(link)
            if not os.path.islink(expanded):
                continue
            target = os.path.realpath(expanded)
            if os.path.exists(target):
                continue
            if any(os.path.commonpath([target, root]) == root for root in module_roots):
                os.unlink(expanded)
                print(f"Removed stale symlink {expanded} (pointed to {target})")

    def find_dangling_links(self, home_modules):
        """Return (link, target) for dangling symlinks into modules/ in the stow dirs.

        Scan dirs are the $HOME counterparts of the directories in the selected
        module trees. Each is listed non-recursively (the nested dirs are scan
        dirs of their own), and symlinked dirs are never entered.
        """
        home = os.path.expanduser("~")
        modules_root = os.path.realpath(MODULES_DIR)
        scan_dirs = set()
        for module in home_modules:
            module_dir = os.path.join(MODULES_DIR, module)
            for root, _, _ in os.walk(module_dir):
                rel = os.path.relpath(root, module_dir)
                scan_dirs.add(os.path.normpath(os.path.join(home, rel)))
        found = []
        for scan_dir in sorted(scan_dirs):
            if os.path.islink(scan_dir) or not os.path.isdir(scan_dir):
                continue
            for name in sorted(os.listdir(scan_dir)):
                path = os.path.join(scan_dir, name)
                if not os.path.islink(path) or os.path.exists(path):
                    continue
                target = os.path.realpath(path)
                if os.path.commonpath([target, modules_root]) == modules_root:
                    found.append((path, target))
        return found

    def prune_dangling_links(self, home_modules):
        # Must run after the unstow: stow removes nearly everything, so only
        # leftovers (links to files no longer in any module) are seen here.
        found = self.find_dangling_links(home_modules)
        if not found:
            return
        print("----------------------------------")
        print("Dangling symlinks into the dotfiles modules were left behind:")
        for path, target in found:
            print(f"  {path} -> {target}")
        try:
            answer = input("Remove them? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Left them in place.")
            return
        for path, _ in found:
            os.unlink(path)
            print(f"Removed {path}")

    def unfold_before_unstow(self, home_modules):
        # A fold left by an older layout over a dir several modules own makes
        # `stow -D` abort ("unstow_contents() called with invalid target").
        for module in home_modules:
            for dir_path in NON_FOLDING_DIRS.get(module, []):
                self.unfold_existing(os.path.expanduser(dir_path), home_modules)

    def ensure_non_folding_dirs(self, home_modules):
        for module in home_modules:
            for dir_path in NON_FOLDING_DIRS.get(module, []):
                expanded = os.path.expanduser(dir_path)
                self.unfold_existing(expanded, home_modules)
                if os.path.lexists(expanded):
                    continue
                os.makedirs(expanded)
                print(f"Pre-created {expanded} as a real directory so stow folds into it "
                      f"instead of symlinking the whole thing")

    def run_stow(self, target, directory, modules, deinit=False, sudo=False):
        if not modules:
            print(f"No modules to stow in {directory}")
            return
        cmd = ["stow", "-t", target, "-d", directory] + modules
        if deinit:
            cmd.insert(1, "-D")
        if sudo:
            cmd.insert(0, "sudo")
        print(f"Attempting to run: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, stdin=subprocess.PIPE)
            print(f"Command finished without errors")
        except subprocess.CalledProcessError as e:
            print(f"Error running stow: {e}")
            sys.exit(1)

    def stow_all(self, home_modules, sys_modules, deinit=False):
        if home_modules:
            self.run_stow(os.environ["HOME"], MODULES_DIR, home_modules, deinit=deinit)
        if sys_modules:
            print("----------------------------------")
            print("System level modules detected in the module list")
            self.run_stow("/", SYS_MODULES_DIR, sys_modules, deinit=deinit, sudo=True)

    def main(self):
        self.ensure_persistent_files()

        if not os.path.isfile(MODULE_LIST_FILE):
            print("file module_list does not exist. Cannot initialize stow")
            print("Did you perhaps forget to rename it?")
            print()
            print("A template .module_list_template exists with the modules that should be loaded in stow")
            print("Copy and rename .module_list_template to module_list and optionally change which modules should get loaded.")
            print("This file will then get ignored by git.")
            sys.exit(0)

        # Load module list
        with open(MODULE_LIST_FILE) as f:
            modules = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        home_modules = [m for m in modules if not m.startswith("sys/")]
        sys_modules = [m.removeprefix("sys/") for m in modules if m.startswith("sys/")]

        if self.args.restow:
            print("--- Unstowing ---")
            self.unfold_before_unstow(home_modules)
            self.stow_all(home_modules, sys_modules, deinit=True)
            self.prune_dangling_links(home_modules)
            print("--- Stowing ---")
            self.remove_stale_links(home_modules)
            self.ensure_non_folding_dirs(home_modules)
            self.stow_all(home_modules, sys_modules, deinit=False)
        else:
            if self.args.deinit:
                self.unfold_before_unstow(home_modules)
            else:
                self.remove_stale_links(home_modules)
                self.ensure_non_folding_dirs(home_modules)
            self.stow_all(home_modules, sys_modules, deinit=self.args.deinit)
            if self.args.deinit:
                self.prune_dangling_links(home_modules)

        if "base" in home_modules:
            self.manage_bashrc(self.args.deinit)
            self.manage_profile(self.args.deinit)

        if not self.args.deinit and "ai" in home_modules:
            self.sync_claude_settings()

    def sync_claude_settings(self):
        # ~/.claude/settings.json isn't stowed (Claude Code writes to it);
        # the tracked settings get merged into it instead. A failure here
        # doesn't undo the stow run, so only warn.
        print("----------------------------------")
        print(f"Syncing Claude settings ({CLAUDE_SETTINGS_SYNC})")
        result = subprocess.run([sys.executable, CLAUDE_SETTINGS_SYNC])
        if result.returncode != 0:
            print(f"Warning: {CLAUDE_SETTINGS_SYNC} failed (exit {result.returncode}); "
                  f"stow itself finished. Fix the above and run it again by hand.")

if __name__ == "__main__":
    StowHelper().main()
