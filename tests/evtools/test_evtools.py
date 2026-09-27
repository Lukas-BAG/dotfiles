"""Tests for the evtools package (the `everything` command).

Run from the repo root:  python3 -m unittest discover tests/evtools
"""

import argparse
import contextlib
import glob
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "modules", "base", ".config", "dotfiles"))

from evtools import check, cli, discovery, entries, goto, new, stats  # noqa: E402

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
                    (".local", "share", "Trash", "Everything"), (".claude", "Everything"),
                    (".local", "share", "containers", "Everything"),
                    ("proj", ".venv", "Everything"), ("proj", "site-packages", "Everything")]:
        mkdir(*skipped)
    touch("proj", ".venv", "pyvenv.cfg")
    os.symlink(main, os.path.join(home, "LinkToEverything"))  # reported, never followed
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
        # keep default-root runs off the real system; subprocesses pass --root instead
        patcher = mock.patch.object(cli, "DEFAULT_ROOT", self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def p(self, *parts):
        return os.path.join(self.home, *parts)


class DiscoveryTest(TreeTest):
    def test_finds_matches_and_skips_pruned_and_symlinks(self):
        self.assertEqual(discovery.find_everything_dirs(self.home, self.home), [
            self.p("Archive", "old_everything"),
            self.p("Main", "Everything"),
            self.p("Main", "Everything", "250001", "sub-everything"),
        ])

    def test_links_reported_never_followed(self):
        a = self.p("a")
        os.makedirs(os.path.join(a, "real-everything"))
        os.symlink(a, os.path.join(a, "loop-everything"))  # loop back to its own parent
        os.symlink(self.p("gone"), os.path.join(a, "broken-everything"))
        os.symlink(os.path.join(a, "self-everything"), os.path.join(a, "self-everything"))
        os.symlink(self.p("everything.txt"), os.path.join(a, "file-everything"))  # not a dir
        os.symlink(self.p("Archive"), os.path.join(a, "plain-link"))  # name doesn't match
        found = discovery.find_everything_dirs(a, self.home, links=True)
        self.assertEqual(found, [os.path.join(a, "loop-everything"),
                                 os.path.join(a, "real-everything")])
        self.assertTrue(discovery.is_link(found[0]))
        self.assertFalse(discovery.is_link(found[1]))
        # no links without the flag; the full tree adds only LinkToEverything
        self.assertEqual(discovery.find_everything_dirs(a, self.home),
                         [os.path.join(a, "real-everything")])
        self.assertEqual(
            set(discovery.find_everything_dirs(self.home, self.home, links=True))
            - set(discovery.find_everything_dirs(self.home, self.home)),
            {self.p("LinkToEverything"), os.path.join(a, "loop-everything")})

    def test_claude_projects_pruned_at_any_depth(self):
        archived = self.p("Main", "Everything", "250003-nry", "home")
        os.makedirs(os.path.join(archived, ".claude", "projects",
                                 "-home-dev-Main-Everything-250001", "sub-everything"))
        os.makedirs(os.path.join(archived, "Main", "Everything"))  # empty, still listed
        os.makedirs(os.path.join(archived, "projects", "everything-kept"))  # not under .claude
        found = discovery.find_everything_dirs(self.home, self.home)
        self.assertFalse([d for d in found if "/.claude/projects" in d], found)
        self.assertIn(os.path.join(archived, "Main", "Everything"), found)
        self.assertIn(os.path.join(archived, "projects", "everything-kept"), found)

    def test_system_and_scratch_dirs_pruned(self):
        skip = discovery.skip_paths(self.home)
        for path in ["/proc", "/usr", "/lib64", "/var/lib", "/snap", "/tmp/claude-1000",
                     self.p(".claude"), self.p(".local", "share")]:
            self.assertTrue(discovery._pruned(path, skip), path)
        for path in ["/var", "/var/tmp", "/tmp", "/tmp/x/claude-1", "/mnt", "/media",
                     self.p(".local"), self.p("Main")]:
            self.assertFalse(discovery._pruned(path, skip), path)

    def test_explicit_root_is_searched_even_if_pruned(self):
        found = discovery.find_everything_dirs(self.p(".claude"), self.home)
        self.assertEqual(found, [self.p(".claude", "Everything")])  # projects still pruned
        self.assertEqual(discovery.find_everything_dirs(self.p("proj", ".venv"), self.home),
                         [self.p("proj", ".venv", "Everything")])

    def test_mounts(self):
        for d in ["disk", "net", "pseudo", "my disk"]:
            os.makedirs(self.p("mnt", d, "Everything"))
        mounts = self.p("mounts")
        with open(mounts, "w") as f:
            f.write(f"/dev/sdb1 {self.p('mnt', 'disk')} ext4 rw 0 0\n"
                    f"srv:/x {self.p('mnt', 'net')} nfs4 rw 0 0\n"
                    f"proc {self.p('mnt', 'pseudo')} proc rw 0 0\n"
                    f"host:/y {self.p('mnt')}/my\\040disk fuse.sshfs rw 0 0\n")

        def find(network=False):
            return discovery.find_everything_dirs(self.p("mnt"), self.home, network, mounts)
        self.assertEqual(find(), [self.p("mnt", "disk", "Everything")])
        self.assertEqual(find(network=True), [self.p("mnt", d, "Everything")
                                              for d in ["disk", "my disk", "net"]])
        self.assertEqual(discovery.pruned_mounts(True, self.p("no-such-file")), set())

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

    @mock.patch.object(discovery, "CACHE_ENABLED", True)
    def test_read_only(self):
        cache_dir = self.p(".cache", "dotfiles")

        def snapshot():
            # every path except the cache dir, the one thing allowed to be written
            return sorted(os.path.join(d, n) for d, dn, fn in os.walk(self.home)
                          for n in dn + fn
                          if not os.path.join(d, n).startswith(cache_dir))
        before = snapshot()
        discovery.find_everything_dirs(self.home, self.home)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["list", "--root", self.home])
        self.assertEqual(snapshot(), before)
        self.assertEqual(os.listdir(cache_dir), ["everything-dirs.json"])


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
        # a dot in the name without an extension doesn't swallow the tags
        s = entries.parse_sidecar("260001-nry_notes_v1.5_@ai")
        self.assertEqual((s.description, s.tags, s.ext), ("notes_v1.5", ("ai",), ""))
        s = entries.parse_sidecar("260001-nry_notes_@v1.2.md")
        self.assertEqual((s.description, s.tags, s.ext), ("notes", ("v1.2",), ".md"))
        for bad in ["260001.md", "260001-nry", "abc_def.md", "26001_x.md"]:
            self.assertIsNone(entries.parse_sidecar(bad), bad)

    def test_list_entries(self):
        names = [e.name for e in entries.list_entries(self.p("Main", "Everything"))]
        self.assertEqual(names, ["250001", "250002-nry", "250003-nry",
                                 "260001-abc", "260001-gel", "260002-lnk"])
        self.assertEqual(entries.list_entries(self.p("does-not-exist")), [])

    def test_list_sidecars(self):
        main = self.p("Main", "Everything")
        open(os.path.join(main, "260001-gel_with spaces_@y.txt"), "w").close()
        open(os.path.join(main, ".mynew-suffix"), "w").close()
        os.mkdir(os.path.join(main, "250003-nry_a_dir"))  # dirs are never sidecars
        os.symlink(os.path.join(main, "250002-nry_first_note_@ai_@x.md"),
                   os.path.join(main, "250003-nry_linked.md"))
        names = [s.name for s in entries.list_sidecars(main)]
        self.assertEqual(names, ["250002-nry_first_note_@ai_@x.md", "250003-nry_linked.md",
                                 "260001-gel_with spaces_@y.txt"])
        self.assertEqual(entries.list_sidecars(self.p("does-not-exist")), [])


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
Everything dirs under ~ (4 found, 1 nested, 1 symlink)

  PATH                                         ENTRIES  NEWEST        SUFFIX
  ~/Archive/old_everything                           0  -             -
  ~/LinkToEverything → ~/Main/Everything             6  260002-lnk    (none),nry,abc,gel,lnk
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

    def test_paths_leave_out_links(self):
        _, out, _ = self.run_cli("list", "--paths")
        self.assertNotIn(self.p("LinkToEverything"), out.splitlines())
        self.assertIn(self.p("Main", "Everything"), out.splitlines())

    def test_broken_and_looping_links(self):
        os.symlink(self.p("gone"), self.p("Archive", "broken-everything"))
        os.symlink(self.p("Archive"), self.p("Archive", "loop-everything"))
        rc, out, err = self.run_cli("list", "--root", self.p("Archive"))
        self.assertEqual((rc, err), (0, ""))
        self.assertNotIn("broken", out)
        self.assertIn("  ~/Archive/loop-everything → ~/Archive ", out)
        self.assertIn("(2 found, 0 nested, 1 symlink)", out)

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
        res = subprocess.run([SHIM, "list", "--paths", "--root", self.home], capture_output=True, text=True,
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

    def test_link_offered_and_printed_as_link_path(self):
        with mock.patch("shutil.which", side_effect=AssertionError("fzf not needed")):
            rc, out, _ = self.run_cli("pick", "linkto")
        self.assertEqual((rc, out), (0, self.p("LinkToEverything") + "\n"))
        rows = []

        def fake_fzf(cmd, input, **kw):
            rows.extend(input.splitlines())
            return subprocess.CompletedProcess(cmd, 130, stdout="")
        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", side_effect=fake_fzf):
            self.run_cli("pick")
        self.assertIn("  ~/LinkToEverything → ~/Main/Everything             6  260002-lnk    "
                      "(none),nry,abc,gel,lnk\t" + self.p("LinkToEverything"), rows)

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
        script = f'everything() {{ "{SHIM}" "$@"; }}; source "{functions}"; cde --root "$HOME" archive && pwd'
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             env={**os.environ, "HOME": self.home})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), [
            "cde: ~/Archive/old_everything", self.p("Archive", "old_everything")])


    def test_cde_via_link_keeps_link_path(self):
        functions = os.path.join(REPO, "modules", "base", ".config", "dotfiles",
                                 "functions.d", "base.sh")
        script = f'everything() {{ "{SHIM}" "$@"; }}; source "{functions}"; cde --root "$HOME" linkto && pwd'
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             env={**os.environ, "HOME": self.home})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), [
            "cde: ~/LinkToEverything", self.p("LinkToEverything")])


class GotoCommandTest(TreeTest):
    run_cli = ListCommandTest.run_cli

    def setUp(self):
        super().setUp()
        self.main = self.p("Main", "Everything")
        self.sub = self.p("Main", "Everything", "250001", "sub-everything")
        for d, name in [(self.main, "250001_Fire_drill.md"), (self.main, "250003-nry_fire_@x.md"),
                        (self.main, "250003-nry_second_fire.md"),  # same entry twice
                        (self.main, "260099_fire_orphan.md"),       # no such entry dir
                        (self.main, "250003-nry_fire.txt"),          # not .md
                        (self.sub, "250001-sub_campfire.md")]:
            open(os.path.join(d, name), "w").close()

    def fake_fzf(self, pick_suffix, calls):
        def run(cmd, input, **kw):
            calls.append((cmd, input))
            line = next(l for l in input.splitlines() if l.endswith(pick_suffix))
            return subprocess.CompletedProcess(cmd, 0, stdout=line + "\n")
        return run

    def test_year_prefix(self):
        self.assertEqual(goto.id_prefix("1", "25"), "250001")
        self.assertEqual(goto.id_prefix("0012", "2025"), "250012")
        self.assertEqual(goto.year_prefix(None), time.strftime("%y"))
        with self.assertRaises(ValueError):
            goto.year_prefix("225")

    def test_id_single_match_in_bookmark(self):
        rc, out, err = self.run_cli("goto", "--label", "ge", "2", "25")
        self.assertEqual((rc, out), (0, self.p("Main", "Everything", "250002-nry") + "\n"))
        self.assertEqual(err, "ge: 250002-nry_first_note_@ai_@x\n")

    def test_id_matches_symlinked_entry_and_bare_name(self):
        rc, out, _ = self.run_cli("goto", "2", "2026")
        self.assertEqual(out, self.p("Main", "Everything", "260002-lnk") + "\n")
        rc, out, err = self.run_cli("goto", "1", "25")
        self.assertEqual(out, self.p("Main", "Everything", "250001") + "\n")
        self.assertEqual(err, "everything goto: 250001_Fire_drill\n")

    def test_id_several_matches_use_fzf(self):
        calls = []
        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", side_effect=self.fake_fzf("260001-gel", calls)):
            rc, out, _ = self.run_cli("goto", "--label", "ge", "1", "26")
        self.assertEqual((rc, out), (0, self.p("Main", "Everything", "260001-gel") + "\n"))
        cmd, fed = calls[0]
        self.assertEqual(fed.splitlines(), [
            "260001-abc\t" + self.p("Main", "Everything", "260001-abc"),
            "260001-gel\t" + self.p("Main", "Everything", "260001-gel")])
        self.assertIn("--prompt=ge: multiple matches for '260001' > ", cmd)

    def test_text_search(self):
        calls = []
        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", side_effect=self.fake_fzf("250001", calls)):
            rc, out, _ = self.run_cli("goto", "FIRE")
        self.assertEqual(out, self.p("Main", "Everything", "250001") + "\n")
        # 250003-nry once despite two sidecars; orphan sidecar, .txt and nested dir left out
        self.assertEqual(calls[0][1].splitlines(), [
            "250001_Fire_drill\t" + self.p("Main", "Everything", "250001"),
            "250003-nry_fire_@x\t" + self.p("Main", "Everything", "250003-nry")])

    def test_all_searches_every_everything_dir(self):
        calls = []
        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", side_effect=self.fake_fzf("250001-sub", calls)):
            rc, out, _ = self.run_cli("goto", "--label", "ge", "-a", "1", "25")
        self.assertEqual((rc, out), (0, self.p(self.sub, "250001-sub") + "\n"))
        cmd, fed = calls[0]
        self.assertEqual(fed.splitlines(), [
            "250001_Fire_drill    ~/Main/Everything\t" + self.p(self.main, "250001"),
            "250001-sub_campfire  ~/Main/Everything/250001/sub-everything\t"
            + self.p(self.sub, "250001-sub")])
        self.assertIn("--prompt=ge: multiple matches for '250001' in any Everything dir > ", cmd)

    def test_all_single_match_names_its_dir(self):
        rc, out, err = self.run_cli("goto", "--label", "ge", "--all", "campfire")
        self.assertEqual((rc, out), (0, self.p(self.sub, "250001-sub") + "\n"))
        self.assertEqual(err, "ge: 250001-sub_campfire  ~/Main/Everything/250001/sub-everything\n")
        # without --all only the bookmark dir is searched
        rc, out, err = self.run_cli("goto", "--label", "ge", "campfire")
        self.assertEqual((rc, out, err), (1, "", "ge: no entry matching 'campfire' found\n"))

    def test_dir_option(self):
        rc, out, _ = self.run_cli("goto", "--dir", self.sub, "1", "25")
        self.assertEqual((rc, out), (0, self.p(self.sub, "250001-sub") + "\n"))
        rc, _, err = self.run_cli("goto", "--dir", self.p("nope"), "x")
        self.assertEqual((rc, err), (1, f"everything goto: not a dir: {self.p('nope')}\n"))

    def test_no_query(self):
        rc, out, _ = self.run_cli("goto")
        self.assertEqual((rc, out), (0, self.main + "\n"))
        rc, out, err = self.run_cli("goto", "--all")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("--all needs an id or text", err)

    def test_errors(self):
        rc, _, err = self.run_cli("goto", "--label", "ge", "1", "225")
        self.assertEqual((rc, err), (1, "ge: invalid year '225' - use e.g. 25 or 2025\n"))
        rc, _, err = self.run_cli("goto", "fire", "drill")
        self.assertEqual(rc, 1)
        self.assertIn("a year only goes with a numeric id", err)
        rc, _, err = self.run_cli("goto", "--label", "ge", "-a", "zzz")
        self.assertEqual((rc, err), (1, "ge: no entry matching 'zzz' found in any Everything dir\n"))
        os.remove(self.p(".sdirs"))
        rc, _, err = self.run_cli("goto", "--label", "ge", "1")
        self.assertEqual((rc, err), (1, "ge: bashmark 'e' is not set to a valid dir (set it with: s e)\n"))

    def test_without_fzf_lists_matches_and_refuses(self):
        with mock.patch("shutil.which", return_value=None):
            rc, out, err = self.run_cli("goto", "--label", "ge", "fire")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("ge: multiple entries match 'fire', refusing:", err)
        self.assertIn(self.p(self.main, "250003-nry"), err)

    def test_fzf_cancel_refuses(self):
        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 130, stdout="")):
            rc, out, err = self.run_cli("goto", "fire")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no selection made", err)

    def test_read_only(self):
        def snapshot():
            return sorted((os.path.join(d, n), os.lstat(os.path.join(d, n)).st_mtime_ns)
                          for d, dn, fn in os.walk(self.home) for n in dn + fn)
        before = snapshot()
        for args in [("2", "25"), ("-a", "campfire"), ("--dir", self.sub, "1", "25"), ()]:
            self.run_cli("goto", *args)
        self.assertEqual(snapshot(), before)

    def run_bash(self, cmd, cwd=None):
        functions = os.path.join(REPO, "modules", "base", ".config", "dotfiles",
                                 "functions.d", "base.sh")
        script = f'everything() {{ "{SHIM}" "$@"; }}; source "{functions}"; {cmd}'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=cwd,
                              env={**os.environ, "HOME": self.home})

    def test_ge_and_gel_change_dir(self):
        res = self.run_bash("ge 2 25 && pwd")
        self.assertEqual((res.returncode, res.stdout, res.stderr),
                         (0, self.p(self.main, "250002-nry") + "\n",
                          "ge: 250002-nry_first_note_@ai_@x\n"))
        res = self.run_bash(f"ge --all --root {self.home} campfire && pwd")
        self.assertEqual(res.stdout, self.p(self.sub, "250001-sub") + "\n")
        res = self.run_bash("ge && pwd")
        self.assertEqual(res.stdout, self.main + "\n")
        res = self.run_bash("gel campfire && pwd", cwd=self.sub)
        self.assertEqual((res.returncode, res.stdout), (0, self.p(self.sub, "250001-sub") + "\n"))

    def test_ge_failure_stays_put(self):
        res = self.run_bash("ge zzz; echo $?; pwd", cwd=self.p("Archive"))
        self.assertEqual(res.stdout.splitlines(), ["1", self.p("Archive")])
        res = self.run_bash("gel; echo $?")
        self.assertEqual((res.stdout, res.stderr), ("1\n", "usage: gel <id-or-text> [year]\n"))


class StatsTest(TreeTest):
    run_cli = ListCommandTest.run_cli

    def setUp(self):
        super().setUp()
        main = self.p("Main", "Everything")
        for name in ["250003-nry_second_@ai.md", "260001-gel_with spaces_@y.txt",
                     "260001-abc_untagged.md", ".mynew-suffix"]:
            open(os.path.join(main, name), "w").close()

    def test_collect(self):
        st = stats.collect([self.p("Main", "Everything"),
                            self.p("Main", "Everything", "250001", "sub-everything")])
        self.assertEqual((st.entries, st.sidecars, st.tagged_sidecars), (7, 4, 3))
        self.assertEqual(dict(st.by_year), {"25": 4, "26": 3})
        # 250001 in Main and 250001-sub in sub-everything are separate ids
        self.assertEqual(st.shared_ids, {(self.p("Main", "Everything"), 260001): ["abc", "gel"]})
        self.assertEqual(len(st.ids), 6)
        self.assertEqual([(n, s.entries, s.ranges) for n, s in st.sorted_suffixes()], [
            ("nry", 2, {"25": ("0002", "0003")}),
            ("(none)", 1, {"25": ("0001", "0001")}),
            ("abc", 1, {"26": ("0001", "0001")}),
            ("gel", 1, {"26": ("0001", "0001")}),
            ("lnk", 1, {"26": ("0002", "0002")}),
            ("sub", 1, {"25": ("0001", "0001")}),
        ])
        self.assertEqual(st.sorted_tags(), [("ai", 2), ("x", 1), ("y", 1)])

    def test_ranges_split_by_year(self):
        d = self.p("Multi-Everything")
        for name in ["250042-nry", "250003-nry", "260008-nry", "260001-nry", "249999-nry"]:
            os.makedirs(os.path.join(d, name))
        st = stats.collect([d])
        self.assertEqual(st.suffixes["nry"].ranges,
                         {"24": ("9999", "9999"), "25": ("0003", "0042"),
                          "26": ("0001", "0008")})

    def test_ids_are_per_dir(self):
        a, b = self.p("Pair", "A-Everything"), self.p("Pair", "B-Everything")
        for d, names in [(a, ["260001-nry"]), (b, ["260001-nry", "260001-gel", "260002",
                                                    "260002-x"])]:
            for name in names:
                os.makedirs(os.path.join(d, name))
        st = stats.collect([a, b])
        self.assertEqual(len(st.ids), 3)
        self.assertEqual(st.shared_ids, {(b, 260001): ["gel", "nry"], (b, 260002): ["(none)", "x"]})
        _, out, _ = self.run_cli("stats", "--dir", "pair")
        self.assertIn("  distinct ids    3   (2 ids are used by several suffixes in the same dir)\n",
                      out)
        _, out, _ = self.run_cli("stats", "--json", "--dir", "b-everything")
        self.assertIn({"dir": b, "id": 260002, "suffixes": [None, "x"]},
                      json.loads(out)["shared_ids"])

    def test_tag_counted_once_per_sidecar(self):
        d = self.p("Dup-Everything")
        os.makedirs(d)
        open(os.path.join(d, "260001_x_@a_@a.md"), "w").close()
        self.assertEqual(stats.collect([d]).tags, {"a": 1})

    def test_empty_tag_ignored(self):
        d = self.p("Empty-Everything")
        os.makedirs(d)
        open(os.path.join(d, "260001_x_@.md"), "w").close()
        open(os.path.join(d, "260002_y_@_@b.md"), "w").close()
        st = stats.collect([d])
        self.assertEqual((st.tags, st.tagged_sidecars), ({"b": 1}, 1))

    def test_text_output(self):
        rc, out, _ = self.run_cli("stats", "--dir", "main")
        self.assertEqual(rc, 0)
        self.assertEqual(out, """\
Everything stats under ~ matching 'main'  (2 dirs, 7 entries, 4 sidecars)

ENTRIES
  total entries   7
  distinct ids    6   (260001 is used by 2 suffixes in ~/Main/Everything: abc, gel)
  by year         2025: 4   2026: 3

SUFFIXES
  SUFFIX  ENTRIES  RANGE
  nry           2  25: 0002–0003
  (none)        1  25: 0001
  abc           1  26: 0001
  gel           1  26: 0001
  lnk           1  26: 0002
  sub           1  25: 0001

TAGS  (3 of 4 sidecars tagged, 3 distinct)
  @ai     2
  @x      1
  @y      1
""")

    def test_per_dir_and_empty_dir(self):
        rc, out, _ = self.run_cli("stats", "--per-dir", "--root", self.p("Archive"))
        self.assertEqual(rc, 0)
        self.assertEqual(out, """\
Everything stats for ~/Archive/old_everything  (1 dir, 0 entries, 0 sidecars)

ENTRIES
  total entries   0
  distinct ids    0
  by year         -

SUFFIXES
  -

TAGS  (0 of 0 sidecars tagged)
  -
""")
        _, out, _ = self.run_cli("stats", "--per-dir")
        self.assertEqual(out.count("Everything stats for "), 3)
        self.assertIn("Everything stats for ~/Main/Everything/250001/sub-everything"
                      "  (1 dir, 1 entry, 0 sidecars)", out)

    def test_single_shared_id_is_named(self):
        for name in ["260001", "260001-b", "260002-b"]:
            os.makedirs(self.p("Solo-Everything", name))
        _, out, _ = self.run_cli("stats", "--dir", "solo")
        self.assertIn("  distinct ids    2   (260001 is used by 2 suffixes: (none), b)\n", out)

    def test_json(self):
        rc, out, _ = self.run_cli("stats", "--json", "--dir", "main")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["dirs"], [self.p("Main", "Everything"),
                                        self.p("Main", "Everything", "250001", "sub-everything")])
        self.assertEqual(data["shared_ids"], [{"dir": self.p("Main", "Everything"),
                                               "id": 260001, "suffixes": ["abc", "gel"]}])
        self.assertEqual(data["by_year"], {"2025": 4, "2026": 3})
        suffixes = {s["suffix"]: s for s in data["suffixes"]}
        self.assertEqual(suffixes["nry"], {"suffix": "nry", "entries": 2,
                                           "ranges": {"2025": ["0002", "0003"]}})
        self.assertEqual(suffixes[None]["entries"], 1)  # no suffix is null, not "(none)"
        self.assertEqual(data["tags"], {"ai": 2, "x": 1, "y": 1})
        _, out, _ = self.run_cli("stats", "--json", "--per-dir")
        self.assertEqual([len(d["dirs"]) for d in json.loads(out)], [1, 1, 1])

    def test_dir_no_match(self):
        rc, out, err = self.run_cli("stats", "--dir", "zzz")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("everything stats: no Everything dir matching 'zzz'", err)

    def test_read_only(self):
        # the discovery cache is the one allowed write, so enable it and leave it out
        cache_dir = os.path.dirname(discovery.cache_file(self.home))
        os.makedirs(cache_dir)  # so creating it doesn't touch ~/.cache's mtime

        def snapshot():
            return sorted((os.path.join(d, n), os.lstat(os.path.join(d, n)).st_mtime_ns)
                          for d, dn, fn in os.walk(self.home) if not d.startswith(cache_dir)
                          for n in dn + fn if os.path.join(d, n) != cache_dir)
        before = snapshot()
        with mock.patch.object(discovery, "CACHE_ENABLED", True):
            for args in [(), ("--per-dir",), ("--json",)]:
                self.run_cli("stats", *args)
        self.assertTrue(os.path.exists(discovery.cache_file(self.home)))
        self.assertEqual(snapshot(), before)


class CacheTest(TreeTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(discovery, "CACHE_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def find(self, root=None, fresh=False):
        return discovery.find_everything_dirs_cached(root or self.home, self.home, fresh=fresh)

    def test_miss_then_hit(self):
        dirs, age = self.find()
        self.assertIsNone(age)
        self.assertEqual(dirs, discovery.find_everything_dirs(self.home, self.home))
        with mock.patch.object(discovery, "find_everything_dirs",
                               side_effect=AssertionError("should use cache")):
            cached, age = self.find()
        self.assertEqual(cached, dirs)
        self.assertGreaterEqual(age, 0)

    def test_new_dir_only_shows_after_expiry_or_fresh(self):
        self.find()
        new = self.p("New-Everything")
        os.mkdir(new)
        self.assertNotIn(new, self.find()[0])
        self.assertIn(new, self.find(fresh=True)[0])
        self.assertIn(new, self.find()[0])  # --fresh wrote the result back

    def test_expiry(self):
        self.find()
        now = time.time()
        with mock.patch("time.time", return_value=now + discovery.CACHE_TTL - 60):
            self.assertIsNotNone(self.find()[1])
        with mock.patch("time.time", return_value=now + discovery.CACHE_TTL + 1):
            self.assertIsNone(self.find()[1])

    def test_stale_paths_dropped_cache_still_valid(self):
        self.find()
        os.rmdir(self.p("Archive", "old_everything", "not-an-entry"))
        os.rmdir(self.p("Archive", "old_everything"))
        dirs, age = self.find()
        self.assertIsNotNone(age)
        self.assertNotIn(self.p("Archive", "old_everything"), dirs)

    def test_links_cached_but_only_returned_on_request(self):
        dirs, _ = self.find()
        self.assertNotIn(self.p("LinkToEverything"), dirs)
        with mock.patch.object(discovery, "find_everything_dirs",
                               side_effect=AssertionError("should use cache")):
            linked, age = discovery.find_everything_dirs_cached(self.home, self.home, links=True)
        self.assertIsNotNone(age)
        self.assertEqual(sorted(set(linked) - set(dirs)), [self.p("LinkToEverything")])

    def test_separate_entry_per_root(self):
        self.find()
        dirs, age = self.find(self.p("Archive"))
        self.assertIsNone(age)
        self.assertEqual(dirs, [self.p("Archive", "old_everything")])
        self.assertEqual(len(self.find()[0]), 3)

    def test_separate_entry_per_network_flag(self):
        self.find()
        dirs, age = discovery.find_everything_dirs_cached(self.home, self.home, network=True)
        self.assertIsNone(age)
        self.assertEqual(dirs, self.find()[0])

    def test_corrupt_cache_is_ignored(self):
        path = discovery.cache_file(self.home)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("{not json")
        dirs, age = self.find()
        self.assertIsNone(age)
        self.assertTrue(dirs)
        self.assertIsNotNone(self.find()[1])  # rewritten as valid JSON

    def test_cli_notice_on_stderr_only(self):
        ListCommandTest.run_cli(self, "list", "--paths")
        rc, out, err = ListCommandTest.run_cli(self, "list", "--paths")
        self.assertEqual(rc, 0)
        self.assertEqual(err, "everything list: using cached Everything-dir list "
                              "(<1 min old; --fresh to re-search)\n")
        self.assertIn(self.p("Main", "Everything"), out.splitlines())
        _, _, err = ListCommandTest.run_cli(self, "pick", "--fresh", "archive")
        self.assertNotIn("cached", err)

    def test_age_format(self):
        self.assertEqual([cli._age(s) for s in (5, 60, 59 * 60, 2 * 3600 + 12 * 60)],
                         ["<1 min", "1 min", "59 min", "2 h 12 min"])



class CacheDisabledTest(TreeTest):
    def test_disabled_always_searches_and_writes_nothing(self):
        with mock.patch.object(discovery, "CACHE_ENABLED", False):
            for _ in range(2):
                rc, _, err = ListCommandTest.run_cli(self, "list", "--paths")
                self.assertEqual((rc, err), (0, ""))
            new = self.p("New-Everything")
            os.mkdir(new)
            self.assertIn(new, discovery.find_everything_dirs_cached(self.home, self.home)[0])
        self.assertFalse(os.path.exists(self.p(".cache", "dotfiles")))


class CheckTest(TreeTest):
    run_cli = ListCommandTest.run_cli

    def setUp(self):
        super().setUp()
        self.d = self.p("Check-Everything")
        os.mkdir(self.d)
        for name in ["260001-ok", "260002-nosc", "260003-two", "260004-txt", "260005-a",
                     "260006-dup", "260006-DUP", "temp", "notes", "260007-x_desc"]:
            os.mkdir(os.path.join(self.d, name))
        for name in ["260001-ok_fine_@ai.md", "260003-two_one.md", "260003-two_other.md",
                     "260004-txt_with spaces_@ai.txt", "260006-dup_x_@ai.md",
                     "260005-b_orphan_@ai.md", "269999_lost_@ai.md",
                     ".mynew-suffix", "README.md", "260005-a.md", ".DS_Store"]:
            open(os.path.join(self.d, name), "w").close()

    def found(self, dirs=None):
        (r,) = check.check([dirs or self.d])
        return {(f.severity, f.code, f.name) for f in r.findings}

    def test_required_checks(self):
        got = {x for x in self.found() if x[0] != check.MINOR}
        self.assertEqual(got, {
            # naming: temp and .mynew-suffix are skipped, everything else checked
            ("major", "bad-dir-name", "notes"),
            ("major", "dir-name-not-id", "260007-x_desc"),
            ("major", "bad-file-name", "README.md"),
            ("major", "bad-file-name", "260005-a.md"),
            ("major", "bad-file-name", ".DS_Store"),
            # dir side
            ("major", "no-sidecar", "260002-nosc"),
            ("major", "no-sidecar", "260005-a"),
            ("medium", "several-sidecars", "260003-two"),
            # sidecar side; names match ignoring case, so both dup dirs share one sidecar
            ("medium", "orphan-sidecar", "260005-b_orphan_@ai.md"),
            ("medium", "orphan-sidecar", "269999_lost_@ai.md"),
            ("major", "several-dirs", "260006-dup_x_@ai.md"),
            ("medium", "not-md", "260004-txt_with spaces_@ai.txt"),
        })

    def test_orphan_names_same_id(self):
        (r,) = check.check([self.d])
        msg = next(f.message for f in r.findings if f.name == "260005-b_orphan_@ai.md")
        self.assertEqual(msg, "no dir 260005-b for this sidecar (same id: 260005-a)")

    def test_clean_dir(self):
        d = self.p("Clean-Everything")
        os.makedirs(os.path.join(d, "260001-a"))
        os.makedirs(os.path.join(d, "temp"))
        open(os.path.join(d, "260001-a_all good_@x.md"), "w").close()
        open(os.path.join(d, ".mynew-suffix"), "w").close()
        self.assertEqual(self.found(d), set())

    def test_minor_checks(self):
        d = self.p("Minor-Everything")
        names = ["260001-a_plain.md", "260002-a_@ai.md", "260003-a_project_ai.md",
                 "260004-a_x_@AI.md", "260005-a_y_@ai .md", "260006-a_z_@.md",
                 "260007-a_about_@rust.md", "260008-a_learning_rust.md"]
        os.mkdir(d)
        for n in names:
            os.mkdir(os.path.join(d, n[:8]))
            open(os.path.join(d, n), "w").close()
        self.assertEqual(self.found(d), {
            ("minor", "no-tags", "260001-a_plain.md"),
            ("minor", "no-description", "260002-a_@ai.md"),  # its tag still counts
            ("minor", "no-tags", "260003-a_project_ai.md"),
            ("minor", "tag-without-at", "260003-a_project_ai.md"),  # short last word
            ("minor", "tag-case", "260004-a_x_@AI.md"),  # @ai is used more often
            ("minor", "tag-trailing-space", "260005-a_y_@ai .md"),
            ("minor", "empty-tag", "260006-a_z_@.md"),
            ("minor", "no-tags", "260006-a_z_@.md"),
            ("minor", "no-tags", "260008-a_learning_rust.md"),
            ("minor", "tag-without-at", "260008-a_learning_rust.md"),  # "rust" is a known tag
        })

    def test_unreadable_dir(self):
        with mock.patch.object(check, "_scan", return_value=None):
            self.assertEqual(self.found(), {("major", "unreadable", ".")})

    def test_text_output_and_exit_code(self):
        rc, out, _ = self.run_cli("check", "--dir", "check-everything")
        self.assertEqual(rc, 1)
        self.assertIn("~/Check-Everything\n  MAJOR\n", out)
        self.assertIn("\n  MEDIUM\n", out)
        self.assertNotIn("MINOR", out)
        self.assertIn("    notes" + " " * 27 + "dir name isn't a <yy><seq>[-suffix] id\n", out)
        self.assertRegex(out.splitlines()[-1],
                         r"^1 Everything dir checked \(matching 'check-everything'\) under ~: "
                         r"8 major, 4 medium; 0 clean \(\d+ minor hidden, --all-levels to show\)$")
        rc, out, _ = self.run_cli("check", "--dir", "check-everything", "--all-levels")
        self.assertIn("\n  MINOR\n", out)
        self.assertNotIn("hidden", out)

    def test_all_dirs_and_clean_exit(self):
        d = self.p("Clean-Everything")
        os.makedirs(os.path.join(d, "260001-a"))
        open(os.path.join(d, "260001-a_ok_@x.md"), "w").close()
        rc, out, _ = self.run_cli("check", "--dir", "clean-everything")
        self.assertEqual((rc, out), (0, "1 Everything dir checked (matching 'clean-everything') "
                                        "under ~: 0 major, 0 medium; 1 clean\n"))
        rc, out, _ = self.run_cli("check")  # every discovered dir, clean ones not listed
        self.assertEqual(rc, 1)
        self.assertIn("~/Main/Everything\n", out)
        self.assertNotIn("~/Clean-Everything", out)
        self.assertRegex(out.splitlines()[-1], r"^5 Everything dirs checked under ~: ")

    def test_json(self):
        rc, out, _ = self.run_cli("check", "--dir", "check-everything", "--json")
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["counts"], {"major": 8, "medium": 4})
        self.assertGreater(data["hidden"], 0)
        (entry,) = data["dirs"]
        self.assertEqual(entry["dir"], self.d)
        self.assertEqual(entry["findings"][0], {
            "severity": "major", "code": "bad-file-name", "name": ".DS_Store",
            "message": "file name doesn't start with <yy><seq>[-suffix]_<description>"})
        rc, out, _ = self.run_cli("check", "--dir", "check-everything", "--json", "--all-levels")
        self.assertIn("minor", json.loads(out)["counts"])

    def test_dir_no_match(self):
        rc, out, err = self.run_cli("check", "--dir", "zzz")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("everything check: no Everything dir matching 'zzz'", err)

    def test_read_only(self):
        cache_dir = os.path.dirname(discovery.cache_file(self.home))
        os.makedirs(cache_dir)

        def snapshot():
            return sorted((os.path.join(d, n), os.lstat(os.path.join(d, n)).st_mtime_ns)
                          for d, dn, fn in os.walk(self.home) if not d.startswith(cache_dir)
                          for n in dn + fn if os.path.join(d, n) != cache_dir)
        before = snapshot()
        with mock.patch.object(discovery, "CACHE_ENABLED", True):
            for args in [(), ("--all-levels",), ("--json",)]:
                self.run_cli("check", *args)
        self.assertEqual(snapshot(), before)


class EntriesCommandTest(TreeTest):
    run_cli = ListCommandTest.run_cli

    def setUp(self):
        super().setUp()
        self.main = self.p("Main", "Everything")
        open(os.path.join(self.main, "260001-gel_gel_notes.md"), "w").close()
        with open(os.path.join(self.main, "250003-nry", "data.bin"), "wb") as f:
            f.write(b"x" * 3000)
        os.makedirs(os.path.join(self.main, "250003-nry", "sub"))
        with open(os.path.join(self.main, "250003-nry", "sub", "more.bin"), "wb") as f:
            f.write(b"x" * 100)
        with open(self.p("elsewhere", "big.bin"), "wb") as f:  # behind the symlinked entry
            f.write(b"x" * 99999)
        self.cd(self.main)

    def cd(self, path):
        old = os.getcwd()
        os.chdir(path)
        self.addCleanup(os.chdir, old)

    def test_enclosing_everything_dir(self):
        find = discovery.enclosing_everything_dir
        self.assertEqual(find(self.main), self.main)
        self.assertEqual(find(os.path.join(self.main, "250003-nry", "sub")), self.main)
        nested = os.path.join(self.main, "250001", "sub-everything")
        self.assertEqual(find(os.path.join(nested, "250001-sub")), nested)  # nearest wins
        self.assertIsNone(find(self.p("elsewhere")))

    def test_tree_size(self):
        self.assertEqual(entries.tree_size(os.path.join(self.main, "250003-nry")), 3100)
        link = os.path.join(self.main, "260002-lnk")
        self.assertEqual(entries.tree_size(link), os.lstat(link).st_size)  # not followed
        self.assertEqual(entries.tree_size(self.p("nope")), 0)

    def test_human_size(self):
        self.assertEqual([cli._human_size(n) for n in [0, 1023, 1536, 20 * 1024, 5 * 2**30]],
                         ["0B", "1023B", "1.5K", "20K", "5.0G"])

    def test_table_from_inside_an_entry(self):
        self.cd(os.path.join(self.main, "250003-nry", "sub"))
        rc, out, _ = self.run_cli("entries")
        self.assertEqual(rc, 0)
        self.assertEqual(out, """\
Entries of ~/Main/Everything (6 entries)

ENTRY       DESCRIPTION  TAGS
250001
250002-nry  first_note   @ai @x
250003-nry
260001-abc
260001-gel  gel_notes
260002-lnk
""")

    def test_not_inside_an_everything_dir(self):
        self.cd(self.p("elsewhere"))
        rc, out, err = self.run_cli("entries")
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(err, "everything entries: not inside an Everything dir "
                              "(cd into one, or use --all)\n")

    def test_size_sorts_largest_first(self):
        rc, out, _ = self.run_cli("entries", "--size")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        link = os.lstat(os.path.join(self.main, "260002-lnk")).st_size
        self.assertEqual(lines[0], "Entries of ~/Main/Everything (6 entries, 3.1K total)")
        self.assertEqual(lines[2].split(), ["ENTRY", "SIZE", "DESCRIPTION", "TAGS"])
        self.assertEqual([line.split()[:2] for line in lines[3:]], [
            ["250003-nry", "3.0K"], ["260002-lnk", f"{link}B"],  # link itself, not target
            ["250001", "0B"], ["250002-nry", "0B"], ["260001-abc", "0B"], ["260001-gel", "0B"]])

    def test_all_from_anywhere(self):
        self.cd(self.p("elsewhere"))
        rc, out, _ = self.run_cli("entries", "--all")
        self.assertEqual(rc, 0)
        self.assertEqual(out, """\
Entries of every Everything dir under ~ (7 entries in 3 dirs)

ENTRY       DESCRIPTION  TAGS    EVERYTHING DIR
250001                           ~/Main/Everything
250001-sub                       ~/Main/Everything/250001/sub-everything
250002-nry  first_note   @ai @x  ~/Main/Everything
250003-nry                       ~/Main/Everything
260001-abc                       ~/Main/Everything
260001-gel  gel_notes            ~/Main/Everything
260002-lnk                       ~/Main/Everything
""")

    def test_empty_dir(self):
        self.cd(self.p("Archive", "old_everything"))
        rc, out, _ = self.run_cli("entries")
        self.assertEqual((rc, out), (0, "Entries of ~/Archive/old_everything (0 entries)\n"))

    def test_read_only(self):
        def snapshot():
            return sorted(os.path.join(d, n) for d, dn, fn in os.walk(self.home) for n in dn + fn)
        before = snapshot()
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["entries", "--size"])
            cli.main(["entries", "--all", "--size"])
        self.assertEqual(snapshot(), before)

    def test_lse_alias(self):
        alias = os.path.join(REPO, "modules", "base", ".config", "dotfiles", "aliases.d", "base.sh")
        with open(alias) as f:
            self.assertIn("alias lse='everything entries'", f.read())


class NewCommandTest(TreeTest):
    def setUp(self):
        super().setUp()
        self.ev = self.p("New", "Everything")
        os.makedirs(self.ev)
        patcher = mock.patch.object(new, "current_year", return_value="26")
        patcher.start()
        self.addCleanup(patcher.stop)

    def mk(self, *names):
        for n in names:
            os.mkdir(os.path.join(self.ev, n))

    def names(self):
        return sorted(os.listdir(self.ev))

    def suffix_file(self, text):
        with open(os.path.join(self.ev, ".mynew-suffix"), "w") as f:
            f.write(text)

    def run_new(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            rc = cli.main(["new", "--dir", self.ev, *args])
        return rc, out.getvalue(), err.getvalue()

    def test_first_use_asks_suffix_and_saves_it(self):
        self.mk("260001-gel")
        rc, out, err = self.run_new("Fire drill", stdin="nry\n")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260002-nry") + "\n"))
        self.assertIn(f"no suffix configured for {self.ev} yet.", err)
        self.assertIn("leave blank for none): ", err)
        self.assertTrue(err.endswith("created '260002-nry/' with sidecar "
                                     "'260002-nry_fire_drill.md'\n"))
        self.assertEqual(self.names(), [".mynew-suffix", "260001-gel", "260002-nry",
                                        "260002-nry_fire_drill.md"])
        with open(os.path.join(self.ev, ".mynew-suffix")) as f:
            self.assertEqual(f.read(), "nry")
        # second time: no question, stdin isn't read
        rc, out, err = self.run_new("again", stdin="ignored\n")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260003-nry") + "\n"))
        self.assertNotIn("suffix", err)

    def test_blank_answer_means_no_suffix(self):
        self.mk("260001")
        rc, out, _ = self.run_new("x y", stdin="\n")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260002") + "\n"))
        self.assertIn("260002_x_y.md", self.names())
        with open(os.path.join(self.ev, ".mynew-suffix")) as f:
            self.assertEqual(f.read(), "")
        self.suffix_file("\n")  # e.g. written by hand: whitespace is ignored
        rc, out, _ = self.run_new("z")
        self.assertEqual(out, os.path.join(self.ev, "260003") + "\n")

    def test_next_id_is_past_highest_of_any_suffix(self):
        self.mk("260001-nry", "260007-abc", "260003", "250099-nry", "temp", "notes")
        self.suffix_file("nry")
        rc, out, err = self.run_new("n")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260008-nry") + "\n"))
        self.assertNotIn("rolled over", err)

    def test_year_rollover(self):
        self.mk("250041-nry", "250042-abc")
        self.suffix_file("nry")
        rc, out, err = self.run_new("n")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260001-nry") + "\n"))
        self.assertIn("everything new: year prefix rolled over (highest "
                      "existing entry is '25', current year is '26') - starting sequence over "
                      "at 0001\n", err)

    def test_description_and_tags(self):
        self.mk("260001")
        self.suffix_file("")
        rc, _, err = self.run_new("  Fire Drill (v2)! Ärger ", "ai", "@x", "@@y")
        self.assertEqual(rc, 0)
        self.assertIn("260002_fire_drill_v2_rger_@ai_@x_@@y.md", self.names())
        self.assertNotIn("note:", err)
        parsed = entries.parse_sidecar("260002_fire_drill_v2_rger_@ai_@x_@@y.md")
        self.assertEqual(parsed.tags, ("ai", "x", "@y"))

    def test_warns_about_tag_without_at(self):
        self.mk("260001")
        self.suffix_file("")
        rc, _, err = self.run_new("working on dotfiles ai")
        self.assertEqual(rc, 0)
        self.assertIn('note: description ends in "_ai", did you mean tag @ai?', err)
        self.assertIn("260002_working_on_dotfiles_ai.md", self.names())

    def test_refuses_outside_everything_dir(self):
        self.mk("temp", "notes", "2600001")
        rc, out, err = self.run_new("x", stdin="nry\n")
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(err, "everything new: no existing <yy><seq>[-suffix] entries found in "
                              f"{self.ev} - this doesn't look like an Everything dir, refusing\n")
        self.assertEqual(self.names(), ["2600001", "notes", "temp"])  # not even the suffix
        rc, _, err = self.run_new("x", "--dir", self.p("nope"))
        self.assertEqual((rc, err), (1, f"everything new: not a dir: {self.p('nope')}\n"))

    def test_never_overwrites(self):
        self.mk("260001-nry")
        self.suffix_file("nry")
        # a real 260002-nry dir would count as an entry and move the id on to 260003
        for existing, make in [("260002-nry", lambda p: os.symlink(self.p("gone"), p)),
                               ("260002-nry", lambda p: open(p, "w").close()),
                               ("260002-nry_other.md", lambda p: open(p, "w").close())]:
            path = os.path.join(self.ev, existing)
            make(path)
            before = self.names()
            rc, out, err = self.run_new("x")
            self.assertEqual((rc, out), (1, ""))
            self.assertEqual(err, f"everything new: '{existing}' already exists, "
                                  "refusing to touch it\n")
            self.assertEqual(self.names(), before)
            os.remove(path)

    def test_other_refusals_write_nothing(self):
        self.mk("260001")
        cases = [
            ((["x"], ""), "no answer, refusing (nothing saved)"),
            ((["x"], "a-b\n"), "suffix 'a-b' may only be letters and digits, refusing"),
            ((["!!!"], "\n"), "description has no letters or digits, refusing"),
            ((["x", "@"], "\n"), "invalid tag '@', refusing"),
            ((["x", "a/b"], "\n"), "invalid tag '@a/b', refusing"),
        ]
        for (args, stdin), msg in cases:
            rc, out, err = self.run_new(*args, stdin=stdin)
            self.assertEqual((rc, out), (1, ""), msg)
            self.assertTrue(err.endswith(f"everything new: {msg}\n"), err)
            self.assertEqual(self.names(), ["260001"])
        self.suffix_file("a b")
        rc, _, err = self.run_new("x")
        self.assertEqual(rc, 1)
        self.assertIn(".mynew-suffix holds 'a b'", err)
        self.suffix_file("")
        self.mk("270001")
        rc, _, err = self.run_new("x")
        self.assertEqual(rc, 1)
        self.assertIn("highest entry '270001' is from a later year than this one ('26')", err)
        self.assertEqual(self.names(), [".mynew-suffix", "260001", "270001"])

    def test_no_ids_left(self):
        self.mk("269999")
        self.suffix_file("")
        rc, _, err = self.run_new("x")
        self.assertEqual((rc, err), (1, "everything new: no ids left for 2026 after "
                                        "'269999', refusing\n"))

    run_bash = GotoCommandTest.run_bash

    def test_mynew_asks_and_changes_dir(self):
        year = time.strftime("%y")  # subprocess: the real year
        self.mk(f"{year}0005-abc")
        res = subprocess.run(
            ["bash", "-c",
             f'everything() {{ "{SHIM}" "$@"; }}; source "{self.functions()}"; '
             'mynew "Hello World" ai && pwd'],
            input="nry\n", capture_output=True, text=True, cwd=self.ev,
            env={**os.environ, "HOME": self.home})
        entry = f"{year}0006-nry"
        self.assertEqual((res.returncode, res.stdout), (0, os.path.join(self.ev, entry) + "\n"),
                         res.stderr)
        self.assertIn("Suffix to use for new entries here", res.stderr)
        self.assertTrue(res.stderr.endswith(f"mynew: created '{entry}/' with sidecar "
                                            f"'{entry}_hello_world_@ai.md'\n"))

    def test_mynew_failure_stays_put(self):
        res = self.run_bash("mynew x; echo $?; pwd", cwd=self.ev)
        self.assertEqual(res.stdout.splitlines(), ["1", self.ev])
        self.assertIn("mynew: no existing <yy><seq>[-suffix] entries found", res.stderr)
        res = self.run_bash("mynew; echo $?")
        self.assertEqual((res.stdout, res.stderr),
                         ("1\n", "mynew: 1 argument required, description (plus optional tags)\n"))

    def functions(self):
        return os.path.join(REPO, "modules", "base", ".config", "dotfiles", "functions.d", "base.sh")


def shell_definitions():
    """{name: body} of every function and alias in the repo's functions.d/aliases.d."""
    defs = {}
    for path in sorted(glob.glob(os.path.join(REPO, "modules", "*", ".config", "*",
                                              "*.d", "*.sh"))):
        if os.path.basename(os.path.dirname(path)) not in ("functions.d", "aliases.d"):
            continue
        # drop comments, so "see ~/.local/bin/everything" doesn't count as a call
        with open(path) as f:
            lines = [re.sub(r"(^|\s)#.*", "", line) for line in f]
        name = None
        for line in lines:
            if name is not None:
                if line.startswith("}"):
                    name = None
                else:
                    defs[name] += line
                continue
            m = re.match(r"alias\s+([^=\s]+)=(.*)", line)
            if m:
                defs[m[1]] = m[2]
                continue
            m = re.match(r"(?:function\s+)?([\w:.-]+)\s*\(\)\s*\{", line)
            if m:
                name = m[1]
                defs[name] = line[m.end():]
    return defs


def everything_wrappers():
    """Public shell functions/aliases that call `everything`, directly or via a helper."""
    defs = shell_definitions()
    calls = re.compile(r"(?:^|[\s;|&(\"'`])everything\s")
    wrappers = {n for n, body in defs.items() if calls.search(body)}
    while True:
        more = {n for n, body in defs.items() if n not in wrappers and any(
            re.search(rf"(?:^|[\s;|&(\"'`]){re.escape(w)}(?:\s|$)", body) for w in wrappers)}
        if not more:
            break
        wrappers |= more
    return {n for n in wrappers if not n.startswith("_")}


class HelpCommandTest(unittest.TestCase):
    def overview_names(self, group):
        return [name for name, _, _ in cli.OVERVIEW[group][1]]

    def test_prints_every_entry_with_example(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["help"]), 0)
        for _, items in cli.OVERVIEW:
            for name, desc, example in items:
                self.assertRegex(out.getvalue(), rf"(?m)^  {re.escape(name)} +{re.escape(desc)}$")
                self.assertIn(f"e.g. {example}\n", out.getvalue())

    def test_every_subcommand_listed(self):
        sub = next(a for a in cli.build_parser()._actions
                   if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(sorted(self.overview_names(0)), sorted(sub.choices))

    def test_every_shell_wrapper_listed(self):
        wrappers = everything_wrappers()
        # guards the parser above: these must be found, or the check is toothless
        self.assertLessEqual({"ge", "gel", "cde", "lse", "mynew"}, wrappers)
        self.assertEqual(sorted(self.overview_names(1)), sorted(wrappers))


class LinkOnlyDirsTest(TreeTest):
    """Everything dirs reachable under --root only via a symlink (ticket 019)."""
    run_cli = ListCommandTest.run_cli

    def setUp(self):
        super().setUp()
        # searched root is ~/Main; the target lives outside it
        self.root = self.p("Main")
        self.ext = self.p("outside", "Ext-Everything")
        os.makedirs(os.path.join(self.ext, "260050-ext"))
        open(os.path.join(self.ext, "260050-ext_external_note_@ai.md"), "w").close()
        self.link = self.p("Main", "ext-everything")
        os.symlink(self.ext, self.link)
        os.symlink(self.ext, self.p("Main", "zz-second-everything"))  # same target again
        os.symlink(self.p("Main", "Everything"), self.p("Main", "dup-everything"))  # found directly

    def test_drop_duplicate_links(self):
        dirs = discovery.find_everything_dirs(self.root, self.home, links=True)
        self.assertEqual(discovery.drop_duplicate_links(dirs), [
            self.p("Main", "Everything"),
            self.p("Main", "Everything", "250001", "sub-everything"),
            self.link,
        ])

    def test_goto_all_finds_entry_via_link(self):
        rc, out, err = self.run_cli("goto", "--all", "--root", self.root, "50", "26")
        self.assertEqual((rc, out), (0, os.path.join(self.link, "260050-ext") + "\n"))
        self.assertEqual(err, "everything goto: 260050-ext_external_note_@ai  ~/Main/ext-everything\n")

    def test_goto_all_no_duplicates_via_link_to_found_dir(self):
        # 250002-nry is in ~/Main/Everything, also reachable as dup-everything
        rc, out, _ = self.run_cli("goto", "--all", "--root", self.root, "2", "25")
        self.assertEqual((rc, out), (0, self.p("Main", "Everything", "250002-nry") + "\n"))

    def test_entries_all(self):
        rc, out, _ = self.run_cli("entries", "--all", "--root", self.root)
        self.assertEqual(rc, 0)
        self.assertIn("(8 entries in 3 dirs)", out)
        self.assertEqual(out.count("260050-ext"), 1)
        self.assertIn("~/Main/ext-everything", out)

    def test_stats_and_check_count_link_once(self):
        _, out, _ = self.run_cli("stats", "--json", "--root", self.root)
        data = json.loads(out)
        self.assertEqual(data["entries"], 8)
        _, out, _ = self.run_cli("check", "--json", "--root", self.root)
        self.assertEqual([d["dir"] for d in json.loads(out)["dirs"]], [
            self.p("Main", "Everything"),
            self.p("Main", "Everything", "250001", "sub-everything"),
            self.link,
        ])

    def test_help_documents_links(self):
        for cmd in ["goto", "entries", "stats", "check"]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                cli.main([cmd, "--help"])
            self.assertIn("only if its target isn't found directly",
                          " ".join(out.getvalue().split()), cmd)


if __name__ == "__main__":
    unittest.main()
