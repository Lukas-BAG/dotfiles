"""Tests for the evtools package (the `everything` command).

Run from the repo root:  python3 -m unittest discover tests/evtools
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "modules", "base", ".config", "dotfiles"))

from evtools import cli, discovery, entries  # noqa: E402

SHIM = os.path.join(REPO, "modules", "base", ".local", "bin", "everything")


def make_tree(home):
    """Fake $HOME with Everything dirs, entries and things discovery must skip."""
    def mkdir(*parts):
        path = os.path.join(home, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def touch(*parts):
        open(os.path.join(home, *parts), "w").close()

    main = mkdir("Main", "Everything")
    for name in ["250001", "250002-nry", "250003-nry", "260001-gel", "260001-abc", "temp"]:
        mkdir("Main", "Everything", name)
    touch("Main", "Everything", "250002-nry_first_note_@ai_@x.md")
    touch("Main", "Everything", "260009-zzz")  # a file, not an entry
    os.symlink(os.path.join(home, "elsewhere"), os.path.join(main, "260002-lnk"))
    mkdir("elsewhere")  # target of the symlinked entry above: counts as an entry
    mkdir("Main", "Everything", "250001", "sub-everything", "250001-sub")  # nested
    mkdir("Archive", "old_everything")  # no entries, lowercase match
    mkdir("Archive", "old_everything", "not-an-entry")

    for skipped in [(".git", "Everything"), ("proj", "node_modules", "Everything"),
                    (".cache", "Everything"), (".claude", "projects", "-home-Everything"),
                    (".local", "share", "Trash", "Everything")]:
        mkdir(*skipped)
    mkdir(".claude", "Everything-kept")  # only .claude/projects is skipped
    os.symlink(main, os.path.join(home, "LinkToEverything"))  # symlinks aren't followed
    touch("everything.txt")  # files never match

    with open(os.path.join(home, ".sdirs"), "w") as f:
        f.write('export DIR_e="/nope"\nexport DIR_e="$HOME/Main/Everything"\n')


class TreeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self._tmp.name)
        make_tree(self.home)
        env = {"HOME": self.home, "SDIRS": os.path.join(self.home, ".sdirs")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def p(self, *parts):
        return os.path.join(self.home, *parts)


class DiscoveryTest(TreeTest):
    def test_finds_matches_and_skips_pruned_and_symlinks(self):
        self.assertEqual(discovery.find_everything_dirs(self.home, self.home), [
            self.p(".claude", "Everything-kept"),
            self.p("Archive", "old_everything"),
            self.p("Main", "Everything"),
            self.p("Main", "Everything", "250001", "sub-everything"),
        ])

    def test_root_itself_can_match(self):
        root = self.p("Main", "Everything")
        self.assertEqual(discovery.find_everything_dirs(root, self.home)[0], root)

    def test_unreadable_dir_is_skipped_silently(self):
        locked = self.p("Archive")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        if os.access(locked, os.R_OK):
            self.skipTest("running as root, permissions not enforced")
        self.assertNotIn(self.p("Archive", "old_everything"),
                         discovery.find_everything_dirs(self.home, self.home))

    def test_bookmark_last_definition_wins(self):
        self.assertEqual(discovery.bookmark_dir(), self.p("Main", "Everything"))

    def test_bookmark_missing_file(self):
        self.assertIsNone(discovery.bookmark_dir(self.p("no-such-sdirs")))

    def test_read_only(self):
        def snapshot():
            return sorted((d, tuple(sorted(dn)), tuple(sorted(fn)))
                          for d, dn, fn in os.walk(self.home))
        before = snapshot()
        discovery.find_everything_dirs(self.home, self.home)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["list", "--root", self.home])
        self.assertEqual(snapshot(), before)


class EntriesTest(TreeTest):
    def test_parse_entry(self):
        e = entries.parse_entry("260003-nry")
        self.assertEqual((e.yy, e.seq, e.suffix, e.id), ("26", "0003", "nry", 260003))
        self.assertIsNone(entries.parse_entry("250001").suffix)
        for bad in ["25001", "2500011", "250001-", "250001-a_b", "temp", "250001-nry_x.md"]:
            self.assertIsNone(entries.parse_entry(bad), bad)

    def test_parse_sidecar(self):
        s = entries.parse_sidecar("250002-nry_first_note_@ai_@x.md")
        self.assertEqual((s.entry.name, s.description, s.tags, s.ext),
                         ("250002-nry", "first_note", ("ai", "x"), ".md"))
        s = entries.parse_sidecar("260001_with spaces.txt")
        self.assertEqual((s.entry.name, s.description, s.tags, s.ext),
                         ("260001", "with spaces", (), ".txt"))
        self.assertEqual(entries.parse_sidecar("260001_no_ext").ext, "")
        for bad in ["260001.md", "260001-nry", "abc_def.md", "26001_x.md"]:
            self.assertIsNone(entries.parse_sidecar(bad), bad)

    def test_list_entries(self):
        names = [e.name for e in entries.list_entries(self.p("Main", "Everything"))]
        self.assertEqual(names, ["250001", "250002-nry", "250003-nry",
                                 "260001-abc", "260001-gel", "260002-lnk"])
        self.assertEqual(entries.list_entries(self.p("does-not-exist")), [])


class ListCommandTest(TreeTest):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_table(self):
        rc, out, _ = self.run_cli("list")
        self.assertEqual(rc, 0)
        self.assertEqual(out, """\
Everything dirs under ~ (4 found, 1 nested)

  PATH                                         ENTRIES  NEWEST        SUFFIX
  ~/.claude/Everything-kept                          0  -             -
  ~/Archive/old_everything                           0  -             -
* ~/Main/Everything                                  6  260002-lnk    (none),nry,abc,gel,lnk
    └ ~/Main/Everything/250001/sub-everything        1  250001-sub    sub

* = bashmark 'e' (used by ge)
""")

    def test_paths(self):
        rc, out, _ = self.run_cli("list", "--paths", "--root", self.p("Main"))
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [
            self.p("Main", "Everything"),
            self.p("Main", "Everything", "250001", "sub-everything"),
        ])

    def test_no_bookmark_no_marker(self):
        os.remove(self.p(".sdirs"))
        _, out, _ = self.run_cli("list")
        self.assertNotIn("*", out)
        self.assertIn("  ~/Main/Everything ", out)

    def test_outside_home_shows_full_paths(self):
        with mock.patch.dict(os.environ, {"HOME": "/nonexistent-home"}):
            _, out, _ = self.run_cli("list", "--root", self.p("Archive"))
        self.assertIn(f"Everything dirs under {self.p('Archive')} (1 found", out)
        self.assertIn(f"  {self.p('Archive', 'old_everything')} ", out)

    def test_errors(self):
        rc, _, err = self.run_cli("list", "--root", self.p("nope"))
        self.assertEqual((rc, err), (1, f"everything list: not a dir: {self.p('nope')}\n"))
        rc, _, err = self.run_cli("list", "--root", self.p("elsewhere"))
        self.assertEqual(rc, 1)
        self.assertIn("no Everything dirs under ~/elsewhere", err)

    def test_shim_runs_from_repo(self):
        res = subprocess.run([SHIM, "list", "--paths"], capture_output=True, text=True,
                             env={**os.environ, "HOME": self.home})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(self.p("Main", "Everything"), res.stdout.splitlines())


class PickCommandTest(TreeTest):
    run_cli = ListCommandTest.run_cli

    def test_single_text_match_prints_path(self):
        with mock.patch("shutil.which", side_effect=AssertionError("fzf not needed")):
            rc, out, err = self.run_cli("pick", "OLD_every")
        self.assertEqual((rc, out), (0, self.p("Archive", "old_everything") + "\n"))
        self.assertIn("only one Everything dir matching 'OLD_every'", err)

    def test_text_matches_whole_path(self):
        rc, out, _ = self.run_cli("pick", "archive")
        self.assertEqual((rc, out), (0, self.p("Archive", "old_everything") + "\n"))

    def test_no_match(self):
        rc, out, err = self.run_cli("pick", "zzz")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no Everything dir matching 'zzz'", err)

    def test_several_matches_use_fzf_with_list_rows(self):
        chosen = self.p("Main", "Everything", "250001", "sub-everything")
        calls = []

        def fake_fzf(cmd, input, **kw):
            calls.append((cmd, input))
            line = next(l for l in input.splitlines() if l.endswith("\t" + chosen))
            return subprocess.CompletedProcess(cmd, 0, stdout=line + "\n")

        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", side_effect=fake_fzf):
            rc, out, _ = self.run_cli("pick", "main")
        self.assertEqual((rc, out), (0, chosen + "\n"))
        cmd, fed = calls[0]
        self.assertEqual(fed.splitlines(), [
            "* ~/Main/Everything                                  6  260002-lnk    "
            "(none),nry,abc,gel,lnk\t" + self.p("Main", "Everything"),
            "    └ ~/Main/Everything/250001/sub-everything        1  250001-sub    sub\t"
            + chosen,
        ])
        self.assertIn("--prompt=multiple matches for 'main' > ", cmd)

    def test_no_text_single_dir_skips_fzf(self):
        with mock.patch("shutil.which", side_effect=AssertionError("fzf not needed")):
            rc, out, err = self.run_cli("pick", "--root", self.p("Archive"))
        self.assertEqual((rc, out), (0, self.p("Archive", "old_everything") + "\n"))
        self.assertIn("only one Everything dir found", err)

    def test_fzf_cancel_refuses(self):
        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 130, stdout="")):
            rc, out, err = self.run_cli("pick")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no selection made", err)

    def test_without_fzf_lists_matches_and_refuses(self):
        with mock.patch("shutil.which", return_value=None):
            rc, out, err = self.run_cli("pick", "main")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("fzf not found", err)
        self.assertIn("~/Main/Everything/250001/sub-everything", err)

    def test_cde_changes_dir(self):
        functions = os.path.join(REPO, "modules", "base", ".config", "dotfiles",
                                 "functions.d", "base.sh")
        script = f'everything() {{ "{SHIM}" "$@"; }}; source "{functions}"; cde archive && pwd'
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             env={**os.environ, "HOME": self.home})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), [
            "cde: ~/Archive/old_everything", self.p("Archive", "old_everything")])


if __name__ == "__main__":
    unittest.main()
