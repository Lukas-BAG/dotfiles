"""Tests for the shared aliases.d handling in init_or_deinit_stow.py.

Run from the repo root:  python3 -m unittest discover tests/shared_aliases
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

import init_or_deinit_stow as ios  # noqa: E402

ALIASES = ".config/dotfiles/aliases.d"


class SharedAliasesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        self.home = os.path.join(root, "home")
        self.modules = os.path.join(root, "modules")
        for module, name in (("base", "base.sh"), ("ai", "ai.sh")):
            d = os.path.join(self.modules, module, ALIASES)
            os.makedirs(d)
            open(os.path.join(d, name), "w").close()
        os.makedirs(os.path.join(self.home, ".config/dotfiles"))
        for patcher in (mock.patch.dict(os.environ, {"HOME": self.home}),
                        mock.patch.object(ios, "MODULES_DIR", self.modules),
                        mock.patch.object(ios, "parse_args")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.helper = ios.StowHelper()
        self.aliases = os.path.join(self.home, ALIASES)

    def test_both_modules_ship_aliases_in_dotfiles_dir(self):
        for module in ("base", "ai"):
            self.assertTrue(os.path.isdir(os.path.join(self.modules, module, ALIASES)))
        self.assertIn("~/.config/dotfiles/aliases.d", ios.NON_FOLDING_DIRS["ai"])
        self.assertIn("~/.config/dotfiles/aliases.d", ios.NON_FOLDING_DIRS["base"])

    def test_folded_aliases_dir_is_unfolded_to_real_dir(self):
        os.symlink(os.path.join(self.modules, "base", ALIASES), self.aliases)
        self.helper.ensure_non_folding_dirs(["base", "ai"])
        self.assertTrue(os.path.isdir(self.aliases))
        self.assertFalse(os.path.islink(self.aliases))

    def test_missing_aliases_dir_is_precreated(self):
        self.helper.ensure_non_folding_dirs(["base", "ai"])
        self.assertTrue(os.path.isdir(self.aliases))
        self.assertFalse(os.path.islink(self.aliases))

    def test_stale_bash_dotfiles_link_removed(self):
        stale = os.path.join(self.home, ".config/bash_dotfiles")
        os.symlink(os.path.join(self.modules, "ai", ".config/bash_dotfiles"), stale)
        self.helper.remove_stale_links(["base", "ai"])
        self.assertFalse(os.path.lexists(stale))

    def test_stale_link_with_existing_target_is_kept(self):
        # e.g. a current-layout link at a path that is also in STALE_LINKS
        stale = os.path.join(self.home, ".config/bash_dotfiles")
        os.symlink(os.path.join(self.modules, "ai", ALIASES), stale)
        self.helper.remove_stale_links(["base", "ai"])
        self.assertTrue(os.path.islink(stale))

    def test_stale_link_pointing_elsewhere_is_kept(self):
        stale = os.path.join(self.home, ".config/bash_dotfiles")
        os.symlink(self.home, stale)
        self.helper.remove_stale_links(["base", "ai"])
        self.assertTrue(os.path.islink(stale))

    def test_real_stale_dir_is_kept(self):
        stale = os.path.join(self.home, ".config/bash_dotfiles")
        os.makedirs(stale)
        self.helper.remove_stale_links(["base", "ai"])
        self.assertTrue(os.path.isdir(stale))


if __name__ == "__main__":
    unittest.main()
