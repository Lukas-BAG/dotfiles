"""Tests for the managed-block helpers in init_or_deinit_stow.py.

Run from the repo root:  python3 -m unittest discover tests/managed_block
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

import init_or_deinit_stow as ios  # noqa: E402

BLOCK = ios.BASHRC_BLOCK
BEGIN, END = ios.block_markers()


class ManagedBlockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.rc = os.path.join(self.home, ".bashrc")

    def read(self, path=None):
        with open(path or self.rc) as f:
            return f.read()

    def test_no_file_creates_block_only(self):
        self.assertEqual(ios.add_managed_block(self.rc, BLOCK), "added")
        self.assertEqual(self.read(), f"{BEGIN}\n{BLOCK}{END}\n")
        self.assertFalse(os.path.exists(self.rc + ios.BACKUP_SUFFIX))

    def test_missing_file_not_seeded_from_skel(self):
        # The caller (manage_bashrc) must not pull in /etc/skel/.bashrc.
        with mock.patch.object(ios, "add_managed_block", wraps=ios.add_managed_block) as m, \
                mock.patch("os.path.expanduser", return_value=self.rc):
            ios.StowHelper.manage_bashrc(mock.Mock(), deinit=False)
        self.assertNotIn("skel", m.call_args.kwargs)
        self.assertEqual(self.read(), f"{BEGIN}\n{BLOCK}{END}\n")

    def test_existing_customizations_kept_block_at_end(self):
        original = "export FOO=1\n# nvm stuff\n"
        with open(self.rc, "w") as f:
            f.write(original)
        ios.add_managed_block(self.rc, BLOCK)
        text = self.read()
        self.assertTrue(text.startswith(original))
        self.assertTrue(text.endswith(f"{BEGIN}\n{BLOCK}{END}\n"))

    def test_existing_without_trailing_newline(self):
        with open(self.rc, "w") as f:
            f.write("export FOO=1")
        ios.add_managed_block(self.rc, BLOCK)
        self.assertTrue(self.read().startswith("export FOO=1\n"))

    def test_block_already_present_is_noop(self):
        ios.add_managed_block(self.rc, BLOCK)
        before = self.read()
        self.assertEqual(ios.add_managed_block(self.rc, BLOCK), "present")
        self.assertEqual(self.read(), before)

    def test_symlink_aborts_without_writing_through(self):
        target = os.path.join(self.home, "tracked")
        with open(target, "w") as f:
            f.write("tracked\n")
        os.symlink(target, self.rc)
        with self.assertRaises(ios.ManagedBlockError) as cm:
            ios.add_managed_block(self.rc, BLOCK)
        self.assertIn("readlink", str(cm.exception))
        self.assertEqual(self.read(target), "tracked\n")
        self.assertTrue(os.path.islink(self.rc))

    def test_backup_made_once(self):
        with open(self.rc, "w") as f:
            f.write("first\n")
        ios.add_managed_block(self.rc, BLOCK)
        backup = self.rc + ios.BACKUP_SUFFIX
        self.assertEqual(self.read(backup), "first\n")
        ios.remove_managed_block(self.rc)
        with open(self.rc, "a") as f:
            f.write("second\n")
        ios.add_managed_block(self.rc, BLOCK)
        self.assertEqual(self.read(backup), "first\n")

    def test_remove_only_removes_block(self):
        original = "export FOO=1\n# nvm stuff\n"
        with open(self.rc, "w") as f:
            f.write(original)
        ios.add_managed_block(self.rc, BLOCK)
        with open(self.rc, "a") as f:
            f.write("export AFTER=1\n")
        self.assertEqual(ios.remove_managed_block(self.rc), "removed")
        self.assertEqual(self.read(), original + "export AFTER=1\n")

    def test_remove_without_block_or_file(self):
        self.assertEqual(ios.remove_managed_block(self.rc), "absent")
        with open(self.rc, "w") as f:
            f.write("x\n")
        self.assertEqual(ios.remove_managed_block(self.rc), "absent")
        self.assertEqual(self.read(), "x\n")

    def test_add_then_remove_roundtrip_on_existing(self):
        original = "export FOO=1\n"
        with open(self.rc, "w") as f:
            f.write(original)
        ios.add_managed_block(self.rc, BLOCK)
        ios.remove_managed_block(self.rc)
        self.assertEqual(self.read(), original)


if __name__ == "__main__":
    unittest.main()


class ProfileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, ".profile")

    def run_manage(self, deinit):
        with mock.patch("os.path.expanduser", return_value=self.path):
            ios.StowHelper.manage_profile(mock.Mock(), deinit=deinit)

    def test_block_added_once_and_existing_lines_kept(self):
        with open(self.path, "w") as f:
            f.write("umask 022\n")
        self.run_manage(False)
        self.run_manage(False)
        with open(self.path) as f:
            text = f.read()
        self.assertTrue(text.startswith("umask 022\n"))
        self.assertEqual(text.count(BEGIN), 1)
        self.assertIn("profile.sh", text)

    def test_symlink_aborts(self):
        target = self.path + ".real"
        open(target, "w").close()
        os.symlink(target, self.path)
        self.run_manage(False)
        self.assertEqual(os.path.getsize(target), 0)

    def test_deinit_removes_only_block(self):
        with open(self.path, "w") as f:
            f.write("umask 022\n")
        self.run_manage(False)
        self.run_manage(True)
        with open(self.path) as f:
            self.assertEqual(f.read(), "umask 022\n")
