"""Tests that desktop/session files live in the i3 module, not in base.

Run from the repo root:  python3 -m unittest discover tests/desktop_split
"""

import os
import re
import sys
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
MODULES = os.path.join(REPO, "modules")

import init_or_deinit_stow as ios  # noqa: E402

DESKTOP_PATHS = [
    ".Xresources", ".xbindkeysrc", ".config/xkb/symbols/custom",
    ".config/dunst/dunstrc", ".config/sway/config",
    ".config/dotfiles/scripts/keyboard-remap.sh",
    "Main/Scripts/Startup_Script/startup_script.sh",
    "Main/Scripts/Helper_Scripts/lock_screen.sh",
    "Main/Scripts/Helper_Scripts/set_background.sh",
    "Main/Data/pexels-dids-3306986.jpg",
    ".local/share/applications/startup_script_with_programs.desktop",
    ".local/bin/myScreenshot",
]


class DesktopSplitTest(unittest.TestCase):
    def test_files_live_in_i3_only(self):
        for rel in DESKTOP_PATHS:
            with self.subTest(rel=rel):
                self.assertTrue(os.path.exists(os.path.join(MODULES, "i3", rel)))
                self.assertFalse(os.path.lexists(os.path.join(MODULES, "base", rel)))

    def test_base_does_not_reference_i3_scripts(self):
        pattern = re.compile(r"Helper_Scripts|Startup_Script|xbindkeys|\.Xresources")
        for root, _, files in os.walk(os.path.join(MODULES, "base")):
            if "__pycache__" in root:
                continue
            for name in files:
                path = os.path.join(root, name)
                with open(path, errors="ignore") as f:
                    self.assertIsNone(pattern.search(f.read()), path)

    def test_claude_usage_alias_only_in_ai_and_only_one_spelling(self):
        def read(module, name):
            with open(os.path.join(MODULES, module, ".config/dotfiles/aliases.d", name)) as f:
                return f.read()
        ai = read("ai", "ai.sh")
        self.assertIn("alias claude_usage=", ai)
        self.assertNotIn("claudeUsage", ai)
        for module, name in (("i3", "i3.sh"), ("base", "base.sh")):
            self.assertNotIn("claude", read(module, name).lower())

    def test_i3_keeps_dirs_it_shares_non_folding(self):
        for d in ("~/.local/bin", "~/.local/share/applications",
                  "~/.config/dotfiles/aliases.d"):
            self.assertIn(d, ios.NON_FOLDING_DIRS["i3"])

    def test_every_moved_top_level_path_is_cleaned_up(self):
        for rel in (".Xresources", ".xbindkeysrc", ".config/xkb", ".config/dunst",
                    ".config/sway", "Main/Data", ".local/bin/myScreenshot"):
            self.assertIn("~/" + rel, ios.STALE_LINKS)


if __name__ == "__main__":
    unittest.main()
