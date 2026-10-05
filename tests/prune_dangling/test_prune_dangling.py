"""Tests for the dangling-symlink prune step in init_or_deinit_stow.py.

Run from the repo root:  python3 -m unittest discover tests/prune_dangling
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

import init_or_deinit_stow as ios  # noqa: E402


class PruneDanglingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        self.home = os.path.join(root, "home")
        self.repo = os.path.join(root, "repo")
        self.outside = os.path.join(root, "outside")
        for d in (self.home, self.outside,
                  os.path.join(self.repo, "modules", "m", ".config", "app"),
                  os.path.join(self.repo, "modules", "m", ".local", "bin"),
                  os.path.join(self.home, ".config", "app"),
                  os.path.join(self.home, ".local", "bin")):
            os.makedirs(d, exist_ok=True)
        self.module_file = os.path.join(self.repo, "modules", "m", ".config", "app", "keep")
        open(self.module_file, "w").close()

        patches = [mock.patch.dict(os.environ, {"HOME": self.home}),
                   mock.patch.object(ios, "MODULES_DIR", os.path.join(self.repo, "modules"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        self.gone = os.path.join(self.repo, "modules", "m", ".config", "app", "removed")
        self.dangling = os.path.join(self.home, ".config", "app", "removed")
        os.symlink(self.gone, self.dangling)
        self.valid = os.path.join(self.home, ".config", "app", "keep")
        os.symlink(self.module_file, self.valid)
        self.foreign = os.path.join(self.home, ".local", "bin", "foreign")
        os.symlink(os.path.join(self.outside, "nothing"), self.foreign)
        self.top = os.path.join(self.home, ".toplevel")
        os.symlink(os.path.join(self.repo, "modules", "m", ".toplevel"), self.top)

    def prune(self, answer):
        with mock.patch("builtins.input", return_value=answer):
            ios.StowHelper.prune_dangling_links(ios.StowHelper.__new__(ios.StowHelper), ["m"])

    def test_removed_after_confirm(self):
        self.prune("y")
        self.assertFalse(os.path.lexists(self.dangling))
        self.assertFalse(os.path.lexists(self.top))

    def test_kept_after_decline(self):
        self.prune("")
        self.assertTrue(os.path.islink(self.dangling))
        self.assertTrue(os.path.islink(self.top))

    def test_eof_counts_as_decline(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            ios.StowHelper.prune_dangling_links(ios.StowHelper.__new__(ios.StowHelper), ["m"])
        self.assertTrue(os.path.islink(self.dangling))

    def test_valid_and_foreign_links_untouched(self):
        self.prune("y")
        self.assertTrue(os.path.islink(self.valid))
        self.assertTrue(os.path.islink(self.foreign))

    def test_nothing_found_does_not_prompt(self):
        os.unlink(self.dangling)
        os.unlink(self.top)
        with mock.patch("builtins.input", side_effect=AssertionError("prompted")):
            ios.StowHelper.prune_dangling_links(ios.StowHelper.__new__(ios.StowHelper), ["m"])

    def test_dirs_outside_module_trees_not_scanned(self):
        other = os.path.join(self.home, "unrelated")
        os.makedirs(other)
        link = os.path.join(other, "stale")
        os.symlink(self.gone, link)
        self.prune("y")
        self.assertTrue(os.path.islink(link))


if __name__ == "__main__":
    unittest.main()
