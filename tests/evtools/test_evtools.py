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
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "modules", "base", ".config", "dotfiles"))

from evtools import check, cli, discovery, entries, goto, info, new, remove, rename, saved_locations, stats  # noqa: E402

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
        # keep `scan` off the real system by default; subprocesses pass --root instead
        patcher = mock.patch.object(cli, "DEFAULT_ROOT", self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        # the saved list `scan` would have produced (links are other paths to saved dirs)
        self.save(*discovery.find_everything_dirs(self.home, self.home))

    def rescan(self):
        """Save every Everything dir now in the fake home, like running `scan` and taking all."""
        self.save(*discovery.find_everything_dirs(self.home, self.home))

    def save(self, *dirs):
        """Replace the saved list of Everything dirs in the fake home."""
        path = saved_locations.list_file(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("".join(d + "\n" for d in dirs))

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

    def test_read_only(self):
        def snapshot():
            return sorted(os.path.join(d, n) for d, dn, fn in os.walk(self.home) for n in dn + fn)
        before = snapshot()
        discovery.find_everything_dirs(self.home, self.home)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["list"])
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
Saved Everything dirs (3 dirs, 1 nested)

  PATH                                         ENTRIES  NEWEST        SUFFIX
  ~/Archive/old_everything                           0  -             -
* ~/Main/Everything                                  6  260002-lnk    (none),nry,abc,gel,lnk
    └ ~/Main/Everything/250001/sub-everything        1  250001-sub    sub

* = bashmark 'e' (used by ge)
""")

    def test_paths(self):
        self.save(self.p("Main", "Everything"),
                  self.p("Main", "Everything", "250001", "sub-everything"))
        rc, out, _ = self.run_cli("list", "--paths")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [
            self.p("Main", "Everything"),
            self.p("Main", "Everything", "250001", "sub-everything"),
        ])

    def test_saved_symlink_shows_target(self):
        self.save(self.p("Archive", "old_everything"), self.p("LinkToEverything"))
        rc, out, err = self.run_cli("list")
        self.assertEqual((rc, err), (0, ""))
        # the e bashmark is the same real dir as the link: listed once, as the link
        self.assertIn("Saved Everything dirs (2 dirs, 0 nested, 1 symlink)", out)
        self.assertIn("* ~/LinkToEverything → ~/Main/Everything  ", out)
        self.assertNotIn("  ~/Main/Everything ", out)
        self.assertIn("* = bashmark 'e'", out)

    def test_same_real_dir_listed_once(self):
        self.save(self.p("LinkToEverything"), self.p("Main", "Everything"))
        _, out, _ = self.run_cli("list", "--paths")
        self.assertEqual(out.splitlines(), [self.p("LinkToEverything")])

    def test_broken_and_looping_links(self):
        os.symlink(self.p("gone"), self.p("Archive", "broken-everything"))
        os.symlink(self.p("Archive"), self.p("Archive", "loop-everything"))
        self.save(self.p("Archive", "broken-everything"), self.p("Archive", "loop-everything"),
                  self.p("Archive", "old_everything"))
        rc, out, err = self.run_cli("list")
        self.assertEqual(rc, 0)
        self.assertEqual(err, "everything list: warning: saved dir ~/Archive/broken-everything "
                              "no longer exists, skipping\n")
        self.assertNotIn("broken", out)
        self.assertIn("  ~/Archive/loop-everything → ~/Archive ", out)
        self.assertIn("(3 dirs, 0 nested, 1 symlink)", out)  # incl. the e bashmark dir

    def test_no_bookmark_no_marker(self):
        os.remove(self.p(".sdirs"))
        _, out, _ = self.run_cli("list")
        self.assertNotIn("*", out)
        self.assertIn("  ~/Main/Everything ", out)

    def test_outside_home_shows_full_paths(self):
        other = self.p("other-home")
        os.makedirs(os.path.join(other, ".config", "dotfiles", "system_local"))
        with open(os.path.join(other, ".config", "dotfiles", "system_local", "everything-dirs"),
                  "w") as f:
            f.write(self.p("Archive", "old_everything") + "\n")
        with mock.patch.dict(os.environ, {"HOME": other, "SDIRS": self.p("no-sdirs")}):
            _, out, _ = self.run_cli("list")
        self.assertIn("Saved Everything dirs (1 dir, 0 nested)", out)
        self.assertIn(f"  {self.p('Archive', 'old_everything')} ", out)

    def test_never_set_up_fails_with_hint(self):
        os.remove(saved_locations.list_file(self.home))
        for cmd in (["list"], ["list", "--paths"], ["pick"], ["goto", "--all", "x"],
                    ["entries", "--all"], ["stats"], ["check"]):
            rc, out, err = self.run_cli(*cmd)
            self.assertEqual((rc, out), (1, ""), cmd)
            self.assertIn("no list of Everything dirs yet - run `everything scan` or "
                          "`everything mark`", err, cmd)

    def test_empty_list_fails(self):
        self.save()
        os.remove(self.p(".sdirs"))
        rc, _, err = self.run_cli("list")
        self.assertEqual(rc, 1)
        self.assertIn("no Everything dirs in the list", err)

    def test_all_commands_never_walk_the_disk(self):
        with mock.patch.object(discovery, "find_everything_dirs",
                               side_effect=AssertionError("walked the disk")):
            for cmd in (["list"], ["pick", "archive"], ["goto", "--all", "2", "25"],
                        ["entries", "--all"], ["stats"], ["check"], ["locations"]):
                self.run_cli(*cmd)

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

    def test_link_offered_and_printed_as_link_path(self):
        self.save(self.p("Archive", "old_everything"), self.p("LinkToEverything"))
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
        self.assertIn("* ~/LinkToEverything → ~/Main/Everything        6  260002-lnk    "
                      "(none),nry,abc,gel,lnk\t" + self.p("LinkToEverything"), rows)

    def test_no_text_single_dir_skips_fzf(self):
        self.save(self.p("Archive", "old_everything"))
        os.remove(self.p(".sdirs"))  # the "e" bashmark dir is searched too
        with mock.patch("shutil.which", side_effect=AssertionError("fzf not needed")):
            rc, out, err = self.run_cli("pick")
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
        script = f'source "{functions}"; everything() {{ "{SHIM}" "$@"; }}; cde archive && pwd'
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             env={**os.environ, "HOME": self.home})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), [
            "cde: ~/Archive/old_everything", self.p("Archive", "old_everything")])


    def test_cde_via_link_keeps_link_path(self):
        self.save(self.p("LinkToEverything"))
        functions = os.path.join(REPO, "modules", "base", ".config", "dotfiles",
                                 "functions.d", "base.sh")
        script = f'source "{functions}"; everything() {{ "{SHIM}" "$@"; }}; cde linkto && pwd'
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
        script = f'source "{functions}"; everything() {{ "{SHIM}" "$@"; }}; {cmd}'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=cwd,
                              env={**os.environ, "HOME": self.home})

    def test_ge_and_gel_change_dir(self):
        res = self.run_bash("ge 2 25 && pwd")
        self.assertEqual((res.returncode, res.stdout, res.stderr),
                         (0, self.p(self.main, "250002-nry") + "\n",
                          "ge: 250002-nry_first_note_@ai_@x\n"))
        res = self.run_bash(f"ge --all campfire && pwd")
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
        self.rescan()
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
Everything stats for the saved dirs matching 'main'  (2 dirs, 7 entries, 4 sidecars)

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
        rc, out, _ = self.run_cli("stats", "--per-dir", "--dir", "archive")
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
        self.rescan()
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
        def snapshot():
            return sorted((os.path.join(d, n), os.lstat(os.path.join(d, n)).st_mtime_ns)
                          for d, dn, fn in os.walk(self.home) for n in dn + fn)
        before = snapshot()
        for args in [(), ("--per-dir",), ("--json",)]:
            self.run_cli("stats", *args)
        self.assertEqual(snapshot(), before)


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
        self.rescan()

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
                         r"^1 Everything dir checked \(matching 'check-everything'\): "
                         r"8 major, 4 medium; 0 clean \(\d+ minor hidden, --all-levels to show\)$")
        rc, out, _ = self.run_cli("check", "--dir", "check-everything", "--all-levels")
        self.assertIn("\n  MINOR\n", out)
        self.assertNotIn("hidden", out)

    def test_all_dirs_and_clean_exit(self):
        d = self.p("Clean-Everything")
        os.makedirs(os.path.join(d, "260001-a"))
        open(os.path.join(d, "260001-a_ok_@x.md"), "w").close()
        self.rescan()
        rc, out, _ = self.run_cli("check", "--dir", "clean-everything")
        self.assertEqual((rc, out), (0, "1 Everything dir checked (matching 'clean-everything'): "
                                        "0 major, 0 medium; 1 clean\n"))
        rc, out, _ = self.run_cli("check")  # every discovered dir, clean ones not listed
        self.assertEqual(rc, 1)
        self.assertIn("~/Main/Everything\n", out)
        self.assertNotIn("~/Clean-Everything", out)
        self.assertRegex(out.splitlines()[-1], r"^5 Everything dirs checked: ")

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
        def snapshot():
            return sorted((os.path.join(d, n), os.lstat(os.path.join(d, n)).st_mtime_ns)
                          for d, dn, fn in os.walk(self.home) for n in dn + fn)
        before = snapshot()
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
Entries of every saved Everything dir (7 entries in 3 dirs)

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

    def run_new(self, *args, stdin="", tty=False, dir=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "stdin", (Tty if tty else io.StringIO)(stdin)):
            rc = cli.main(["new", "--dir", dir or self.ev, *args])
        return rc, out.getvalue(), err.getvalue()

    def devbox(self, text):
        with open(self.p(".devbox_id"), "w") as f:
            f.write(text)

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
        self.assertEqual(err, "everything new: 'Everything' has no <yy><seq>[-suffix] entries "
                              "but contains other files (3 items), so it isn't treated as a "
                              "new Everything dir; use an empty dir, or add a first entry by "
                              "hand\n")
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
             f'source "{self.functions()}"; everything() {{ "{SHIM}" "$@"; }}; '
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
        open(os.path.join(self.ev, "notes.txt"), "w").close()
        res = self.run_bash("mynew x; echo $?; pwd", cwd=self.ev)
        self.assertEqual(res.stdout.splitlines(), ["1", self.ev])
        self.assertIn("mynew: 'Everything' has no <yy><seq>[-suffix] entries but contains "
                      "other files (1 item)", res.stderr)
        res = self.run_bash("mynew; echo $?")
        self.assertEqual((res.stdout, res.stderr),
                         ("1\n", "mynew: 1 argument required, description (plus optional tags)\n"))

    # starting a new Everything dir (ticket 025)

    def plain(self, *names):
        d = self.p("New", "plain")
        os.makedirs(d)
        for n in names:
            open(os.path.join(d, n), "w").close()
        return d

    def test_empty_everything_dir_gets_its_first_entry(self):
        rc, out, err = self.run_new("First idea", "ai", stdin="nry\n")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260001-nry") + "\n"))
        self.assertNotIn("year prefix rolled over", err)
        self.assertEqual(self.names(), [".mynew-suffix", "260001-nry", "260001-nry_first_idea_@ai.md"])

    def test_dir_with_only_a_suffix_file_counts_as_empty(self):
        self.suffix_file("nry")
        rc, out, _ = self.run_new("x")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260001-nry") + "\n"))

    def test_empty_dir_without_everything_in_name_asks_why(self):
        d = self.plain()
        rc, out, err = self.run_new("x", stdin="y\n", dir=d)  # no terminal
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("'plain' is empty and its name doesn't contain 'everything'; start a "
                      "new Everything dir here? - no terminal to ask on, refusing (use --yes)",
                      err)
        self.assertEqual(os.listdir(d), [])
        rc, out, err = self.run_new("x", "--yes", stdin="nry\n", dir=d)
        self.assertEqual((rc, out), (0, os.path.join(d, "260001-nry") + "\n"))

    def test_empty_dir_confirm_yes_and_no(self):
        d = self.plain()
        rc, _, err = self.run_new("x", stdin="n\n", tty=True, dir=d)
        self.assertEqual(rc, 1)
        self.assertIn("start a new Everything dir here? [y/N] ", err)
        self.assertTrue(err.endswith("everything new: not started\n"))
        self.assertEqual(os.listdir(d), [])
        rc, out, _ = self.run_new("x", stdin="y\nnry\n", tty=True, dir=d)
        self.assertEqual((rc, out), (0, os.path.join(d, "260001-nry") + "\n"))

    def test_case_insensitive_everything_name_needs_no_question(self):
        d = self.p("New", "my-EVERYTHING-notes")
        os.makedirs(d)
        rc, _, err = self.run_new("x", stdin="nry\n", dir=d)
        self.assertEqual(rc, 0)
        self.assertNotIn("start a new Everything dir", err)

    def test_non_empty_dir_without_entries_is_still_refused(self):
        d = self.plain("a.txt", "b.txt")
        rc, out, err = self.run_new("x", "--yes", stdin="nry\n", dir=d)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("'plain' has no <yy><seq>[-suffix] entries but contains other files "
                      "(2 items)", err)
        self.assertEqual(sorted(os.listdir(d)), ["a.txt", "b.txt"])

    # suffix sources

    def test_devbox_suffix_is_used_without_asking_and_saved(self):
        self.devbox("nry\n")
        rc, out, err = self.run_new("x", stdin="ignored\n")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260001-nry") + "\n"))
        self.assertIn("everything new: using suffix 'nry' from ~/.devbox_id\n", err)
        self.assertNotIn("Suffix to use", err)
        with open(os.path.join(self.ev, ".mynew-suffix")) as f:
            self.assertEqual(f.read(), "nry")
        os.remove(self.p(".devbox_id"))  # later runs don't depend on it
        rc, out, err = self.run_new("y")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260002-nry") + "\n"))
        self.assertNotIn("devbox", err)

    def test_invalid_devbox_content_refuses(self):
        self.mk("260001")
        self.devbox("a-b\n")
        rc, out, err = self.run_new("x", stdin="nry\n")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("~/.devbox_id holds 'a-b', but a suffix may only be letters and digits",
                      err)
        self.assertEqual(self.names(), ["260001"])

    def test_blank_devbox_file_is_ignored(self):
        self.mk("260001")
        self.devbox("  \n")
        rc, _, err = self.run_new("x", stdin="nry\n")
        self.assertEqual(rc, 0)
        self.assertIn("Suffix to use for new entries here", err)

    def test_existing_suffix_file_wins_over_devbox(self):
        self.mk("260001")
        self.devbox("box\n")
        self.suffix_file("mine")
        rc, out, err = self.run_new("x")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260002-mine") + "\n"))
        self.assertNotIn("devbox", err)
        self.suffix_file("")  # "no suffix" is a choice too
        rc, out, _ = self.run_new("y")
        self.assertEqual(out, os.path.join(self.ev, "260003") + "\n")

    def test_suffix_flag_is_for_one_entry_only(self):
        self.mk("260001")
        self.suffix_file("keep")
        self.devbox("box\n")
        rc, out, err = self.run_new("x", "--suffix", "once")
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260002-once") + "\n"))
        self.assertNotIn("devbox", err)
        with open(os.path.join(self.ev, ".mynew-suffix")) as f:
            self.assertEqual(f.read(), "keep")
        rc, _, _ = self.run_new("y", "--suffix", "")  # explicit "none"
        self.assertEqual(rc, 0)
        self.assertIn("260003_y.md", self.names())

    def test_suffix_flag_saves_nothing_in_a_new_dir(self):
        rc, _, _ = self.run_new("x", "--suffix", "abc")
        self.assertEqual((rc, self.names()), (0, ["260001-abc", "260001-abc_x.md"]))

    def test_suffix_flag_is_validated(self):
        self.mk("260001")
        rc, _, err = self.run_new("x", "--suffix", "a-b")
        self.assertEqual(rc, 1)
        self.assertIn("suffix 'a-b' may only be letters and digits", err)
        self.assertEqual(self.names(), ["260001"])

    def test_ask_suffix_replaces_the_saved_one(self):
        self.mk("260001-old")
        self.suffix_file("old")
        self.devbox("box\n")
        rc, out, err = self.run_new("x", "--ask-suffix", stdin="new\n", tty=True)
        self.assertEqual((rc, out), (0, os.path.join(self.ev, "260002-new") + "\n"))
        self.assertIn("Suffix to use for new entries here", err)
        with open(os.path.join(self.ev, ".mynew-suffix")) as f:
            self.assertEqual(f.read(), "new")

    def test_ask_suffix_needs_a_terminal_and_a_valid_answer(self):
        self.mk("260001")
        rc, _, err = self.run_new("x", "--ask-suffix", stdin="nry\n")
        self.assertEqual(rc, 1)
        self.assertIn("--ask-suffix needs a terminal", err)
        rc, _, err = self.run_new("x", "--ask-suffix", stdin="a b\n", tty=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self.names(), ["260001"])

    def test_suffix_flags_cannot_be_combined(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.main(["new", "--dir", self.ev, "x", "--suffix", "a", "--ask-suffix"])
        self.assertIn("not allowed with", err.getvalue())
        self.assertEqual(self.names(), [])

    def test_mynew_in_empty_dir_from_shell(self):
        self.devbox("nry\n")
        res = subprocess.run(
            ["bash", "-c", f'source "{self.functions()}"; everything() {{ "{SHIM}" "$@"; }}; '
             'mynew "Hello" && pwd'],
            capture_output=True, text=True, cwd=self.ev, stdin=subprocess.DEVNULL,
            env={**os.environ, "HOME": self.home})
        year = time.strftime("%y")
        self.assertEqual((res.returncode, res.stdout),
                         (0, os.path.join(self.ev, f"{year}0001-nry") + "\n"), res.stderr)

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


class Tty(io.StringIO):
    """stdin that claims to be a terminal, answering from the given text."""
    def isatty(self):
        return True


class SavedLocationsTest(TreeTest):
    def setUp(self):
        super().setUp()
        os.remove(saved_locations.list_file(self.home))

    def listed(self):
        return saved_locations.load()

    def test_missing_file_is_not_set_up(self):
        self.assertIsNone(self.listed())
        with self.assertRaises(saved_locations.NotSetUp):
            saved_locations.all_dirs()

    def test_list_file_lives_in_system_local(self):
        self.assertEqual(saved_locations.list_file(self.home), self.p(
            ".config", "dotfiles", "system_local", "everything-dirs"))

    def test_add_creates_file_and_dir_and_dedupes(self):
        d = self.p("Main", "Everything")
        self.assertTrue(saved_locations.add(d))
        self.assertFalse(saved_locations.add(d))
        self.assertEqual(self.listed(), [d])
        # another path to the same real dir counts as saved
        self.assertFalse(saved_locations.add(self.p("LinkToEverything")))
        self.assertEqual(self.listed(), [d])

    def test_comments_and_blank_lines_are_ignored_and_kept(self):
        path = saved_locations.list_file(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(f"# mine\n\n  {self.p('Archive')}  \n# note\n")
        self.assertEqual(self.listed(), [self.p("Archive")])
        saved_locations.add(self.p("Main"))
        with open(path) as f:
            self.assertEqual(f.read(), f"# mine\n# note\n{self.p('Archive')}\n{self.p('Main')}\n")

    def test_remove(self):
        saved_locations.add(self.p("Archive"))
        saved_locations.add(self.p("Main"))
        self.assertTrue(saved_locations.remove(self.p("Archive")))
        self.assertFalse(saved_locations.remove(self.p("Archive")))
        self.assertEqual(self.listed(), [self.p("Main")])

    def test_write_leaves_no_temp_files(self):
        saved_locations.add(self.p("Archive"))
        saved_locations.remove(self.p("Archive"))
        self.assertEqual(os.listdir(os.path.dirname(saved_locations.list_file(self.home))),
                         ["everything-dirs"])

    def test_all_dirs_adds_bookmark_once_and_reports_missing(self):
        main = self.p("Main", "Everything")
        saved_locations.add(self.p("LinkToEverything"))  # same real dir as the bookmark
        saved_locations.add(self.p("Archive", "old_everything"))
        saved_locations.add(self.p("gone"))
        found = saved_locations.all_dirs()
        self.assertEqual(found.dirs, [self.p("Archive", "old_everything"),
                                      self.p("LinkToEverything")])
        self.assertEqual(found.missing, [self.p("gone")])
        self.assertNotIn(main, found.dirs)

    def test_all_dirs_with_bookmark_in_list(self):
        main = self.p("Main", "Everything")
        saved_locations.add(main)
        self.assertEqual(saved_locations.all_dirs().dirs, [main])

    def test_missing_dir_warning_on_all_commands(self):
        self.save(self.p("gone"), self.p("Main", "Everything"))
        for cmd in (["list"], ["pick"], ["entries", "--all"], ["stats"], ["check"],
                    ["goto", "--all", "2", "25"]):
            _, _, err = ListCommandTest.run_cli(self, *cmd)
            self.assertIn("everything %s: warning: saved dir ~/gone no longer exists, skipping\n"
                          % cmd[0], err, cmd)


class EnclosingDirTest(TreeTest):
    def test_marked_dir_counts_even_without_everything_in_name(self):
        d = self.p("Work", "notes")
        os.makedirs(os.path.join(d, "deeper"))
        self.assertIsNone(discovery.enclosing_everything_dir(os.path.join(d, "deeper")))
        self.assertEqual(discovery.enclosing_everything_dir(os.path.join(d, "deeper"), [d]), d)
        # compared by real path, so a link to a marked dir works too
        os.symlink(d, self.p("notes-link"))
        self.assertEqual(discovery.enclosing_everything_dir(self.p("notes-link", "deeper"), [d]),
                         self.p("notes-link"))

    def test_nested_dir_needs_its_own_mark(self):
        outer, inner = self.p("Work", "outer"), self.p("Work", "outer", "inner")
        os.makedirs(inner)
        self.assertEqual(discovery.enclosing_everything_dir(inner, [outer]), outer)
        self.assertEqual(discovery.enclosing_everything_dir(inner, [outer, inner]), inner)

    def test_lse_in_a_marked_dir(self):
        d = self.p("Work", "notes")
        os.makedirs(os.path.join(d, "260001-x"))
        os.chdir(d)
        self.addCleanup(os.chdir, "/")
        self.assertEqual(ListCommandTest.run_cli(self, "entries")[0], 1)
        saved_locations.add(d)
        rc, out, _ = ListCommandTest.run_cli(self, "entries")
        self.assertEqual(rc, 0)
        self.assertIn("260001-x", out)


class LocationCommandsTest(TreeTest):
    run_cli = ListCommandTest.run_cli

    def run_in(self, args, stdin="", tty=False, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(cwd or self.home)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    mock.patch.object(sys, "stdin", (Tty if tty else io.StringIO)(stdin)), \
                    mock.patch.dict(os.environ, {"PWD": cwd or self.home}), \
                    mock.patch("shutil.which", return_value=None):  # no fzf
                rc = cli.main(args)
        finally:
            os.chdir(old)
        return rc, out.getvalue(), err.getvalue()

    def setUp(self):
        super().setUp()
        self.empty()

    def empty(self):
        os.remove(saved_locations.list_file(self.home))
        os.remove(self.p(".sdirs"))  # no "e" bashmark dir either

    def listed(self):
        return saved_locations.load()

    # scan

    def test_scan_creates_list_and_adds_chosen(self):
        rc, out, err = self.run_in(["scan"], "1 3\n")
        self.assertEqual((rc, out), (0, ""))
        # every Everything dir in the fake home, sorted; none saved yet
        self.assertIn("  1) ~/Archive/old_everything\n", err)
        found = discovery.drop_duplicate_links(
            discovery.find_everything_dirs(self.home, self.home, links=True))
        self.assertEqual(self.listed(), [found[0], found[2]])
        self.assertIn("added 2 dirs", err)
        self.assertIn("created ~/.config/dotfiles/system_local/everything-dirs", err)

    def test_scan_all_and_none(self):
        found = discovery.drop_duplicate_links(
            discovery.find_everything_dirs(self.home, self.home, links=True))
        self.run_in(["scan"], "\n")
        self.assertEqual(self.listed(), [])  # nothing chosen, but the file exists
        self.run_in(["scan"], "all\n")
        self.assertEqual(self.listed(), found)

    def test_scan_shows_saved_dirs_and_only_adds_new_ones(self):
        saved_locations.add(self.p("Main", "Everything"))
        rc, _, err = self.run_in(["scan"], "all\n")
        self.assertIn("~/Main/Everything  (saved)\n", err)
        self.assertEqual(self.listed().count(self.p("Main", "Everything")), 1)
        rc, _, err = self.run_in(["scan"])  # now everything is saved
        self.assertEqual(rc, 0)
        self.assertIn("all already saved", err)

    def test_scan_root_and_errors(self):
        rc, _, err = self.run_in(["scan", "--root", self.p("nope")])
        self.assertEqual((rc, err), (1, f"everything scan: not a dir: {self.p('nope')}\n"))
        rc, _, err = self.run_in(["scan", "--root", self.p("Archive")], "1\n")
        self.assertEqual(self.listed(), [self.p("Archive", "old_everything")])
        rc, _, err = self.run_in(["scan", "--root", self.p("elsewhere")])
        self.assertEqual(rc, 1)
        self.assertIn("no Everything dirs under ~/elsewhere", err)

    def test_scan_bad_answer_adds_nothing(self):
        rc, _, err = self.run_in(["scan"], "99\n")
        self.assertEqual(self.listed(), [])
        self.assertIn("'99' is not one of 1-", err)

    def test_scan_fzf_multi_select(self):
        rows = []

        def fake_fzf(cmd, input, **kw):
            rows.extend(input.splitlines())
            self.assertIn("--multi", cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=rows[0] + "\n" + rows[1] + "\n")
        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", side_effect=fake_fzf):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(cli.main(["scan"]), 0)
        self.assertEqual(self.listed(), [r.split("\t")[1] for r in rows[:2]])

    # locations

    def test_locations(self):
        saved_locations.add(self.p("Archive", "old_everything"))
        saved_locations.add(self.p("gone"))
        rc, out, _ = self.run_in(["locations"])
        self.assertEqual((rc, out), (0, "~/Archive/old_everything\n~/gone  (missing)\n"))

    def test_locations_flags_unsaved_bookmark(self):
        with open(self.p(".sdirs"), "w") as f:
            f.write('export DIR_e="$HOME/Main/Everything"\n')
        saved_locations.add(self.p("Archive", "old_everything"))
        _, out, _ = self.run_in(["locations"])
        self.assertEqual(out.splitlines(), [
            "~/Archive/old_everything",
            "~/Main/Everything  (bashmark 'e', searched too, not in the list)"])
        saved_locations.add(self.p("Main", "Everything"))
        _, out, _ = self.run_in(["locations"])
        self.assertNotIn("bashmark", out)

    def test_locations_never_set_up(self):
        rc, out, err = self.run_in(["locations"])
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("run `everything scan` or `everything mark`", err)

    # mark

    def test_mark_everything_named_dir_needs_no_question(self):
        rc, out, err = self.run_in(["mark", self.p("Archive", "old_everything")])
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(err, "everything mark: added ~/Archive/old_everything\n")
        self.assertEqual(self.listed(), [self.p("Archive", "old_everything")])

    def test_mark_current_dir_by_default_and_dir_with_entries(self):
        d = self.p("Work", "notes")
        os.makedirs(os.path.join(d, "260001-x"))
        rc, _, _ = self.run_in(["mark"], cwd=d)
        self.assertEqual((rc, self.listed()), (0, [d]))

    def test_mark_twice_is_a_noop(self):
        self.run_in(["mark", self.p("Archive", "old_everything")])
        rc, _, err = self.run_in(["mark", self.p("Archive", "old_everything")])
        self.assertEqual(rc, 0)
        self.assertIn("is already in the list", err)
        self.assertEqual(self.listed().count(self.p("Archive", "old_everything")), 1)

    def test_mark_odd_dir_asks_and_says_why(self):
        d = self.p("Work", "plain")
        os.makedirs(d)
        rc, _, err = self.run_in(["mark", d], "n\n", tty=True)
        self.assertEqual(rc, 1)
        self.assertIn("doesn't look like an Everything dir: its name doesn't contain "
                      "\"everything\" and it holds no <yy><seq>[-suffix] entry dirs", err)
        self.assertIn("not marked", err)
        self.assertIsNone(self.listed())
        rc, _, _ = self.run_in(["mark", d], "y\n", tty=True)
        self.assertEqual((rc, self.listed()), (0, [d]))

    def test_mark_odd_dir_without_tty_needs_yes(self):
        d = self.p("Work", "plain")
        os.makedirs(d)
        rc, _, err = self.run_in(["mark", d], "y\n")
        self.assertEqual(rc, 1)
        self.assertIn("no terminal to ask on, refusing (use --yes)", err)
        self.assertIsNone(self.listed())
        rc, _, _ = self.run_in(["mark", "--yes", d])
        self.assertEqual((rc, self.listed()), (0, [d]))

    def test_mark_not_a_dir(self):
        rc, _, err = self.run_in(["mark", self.p("nope")])
        self.assertEqual((rc, err), (1, f"everything mark: not a dir: {self.p('nope')}\n"))

    def test_mark_makes_everything_commands_work(self):
        rc, _, err = self.run_in(["list"])
        self.assertEqual(rc, 1)
        self.run_in(["mark", self.p("Archive", "old_everything")])
        rc, out, _ = self.run_in(["list", "--paths"])
        self.assertEqual((rc, out), (0, self.p("Archive", "old_everything") + "\n"))

    # unmark

    def fill(self):
        for d in ("Archive/old_everything", "Main/Everything", "Main/Everything/250001/sub-everything"):
            saved_locations.add(self.p(*d.split("/")))

    def test_unmark_single_match_confirms(self):
        self.fill()
        rc, _, err = self.run_in(["unmark", "archive"], "y\n", tty=True)
        self.assertEqual(rc, 0)
        self.assertIn("Remove ~/Archive/old_everything from the list? [y/N] ", err)
        self.assertEqual(self.listed(), [self.p("Main", "Everything"),
                                         self.p("Main", "Everything", "250001", "sub-everything")])

    def test_unmark_declined_or_no_tty_keeps_it(self):
        self.fill()
        for stdin, tty in (("n\n", True), ("\n", True), ("y\n", False)):
            rc, _, err = self.run_in(["unmark", "archive"], stdin, tty=tty)
            self.assertEqual(rc, 1)
            self.assertIn("not removed", err)
        self.assertEqual(len(self.listed()), 3)

    def test_unmark_several_matches_picks_then_confirms(self):
        self.fill()
        rc, _, err = self.run_in(["unmark", "main"], "2\ny\n", tty=True)
        self.assertEqual(rc, 0)
        self.assertIn("  1) ~/Main/Everything\n  2) ~/Main/Everything/250001/sub-everything\n", err)
        self.assertEqual(self.listed(), [self.p("Archive", "old_everything"),
                                         self.p("Main", "Everything")])

    def test_unmark_without_text_picks_from_all(self):
        self.fill()
        rc, _, _ = self.run_in(["unmark"], "1\ny\n", tty=True)
        self.assertEqual(rc, 0)
        self.assertNotIn(self.p("Archive", "old_everything"), self.listed())

    def test_unmark_no_selection_and_no_match(self):
        self.fill()
        rc, _, err = self.run_in(["unmark"], "\n", tty=True)
        self.assertEqual(rc, 1)
        self.assertIn("no selection made", err)
        rc, _, err = self.run_in(["unmark", "zzz"], "y\n", tty=True)
        self.assertEqual(rc, 1)
        self.assertIn("no Everything dir matching 'zzz'", err)
        self.assertEqual(len(self.listed()), 3)

    def test_unmark_fzf_picker(self):
        self.fill()

        def fake_fzf(cmd, input, **kw):
            line = next(l for l in input.splitlines() if l.endswith("sub-everything"))
            return subprocess.CompletedProcess(cmd, 0, stdout=line + "\n")
        with mock.patch("shutil.which", return_value="/usr/bin/fzf"), \
                mock.patch("subprocess.run", side_effect=fake_fzf), \
                mock.patch.object(sys, "stdin", Tty("y\n")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["unmark"]), 0)
        self.assertEqual(len(self.listed()), 2)

    def test_unmark_never_set_up(self):
        rc, _, err = self.run_in(["unmark"], "y\n", tty=True)
        self.assertEqual(rc, 1)
        self.assertIn("run `everything scan` or `everything mark`", err)


class RenameCommandTest(TreeTest):
    """`everything rename`, with $EDITOR stubbed by a script that replays EDITS."""

    STUB = (
        "import sys\n"
        "queue, target = sys.argv[1], sys.argv[2]\n"
        "edits = open(queue).read().split('\\n')[:-1]\n"
        "if not edits:\n"
        "    sys.exit('editor opened more often than expected')\n"
        "first, rest = edits[0], edits[1:]\n"
        "open(queue, 'w').write(''.join(e + '\\n' for e in rest))\n"
        "open(queue + '.seen', 'a').write(open(target).read() + '----\\n')\n"
        "if first == '!FAIL':\n"
        "    sys.exit(1)\n"
        "if first != '!KEEP':\n"
        "    lines = open(target).read().split('\\n')\n"
        "    lines[0] = first\n"
        "    open(target, 'w').write('\\n'.join(lines))\n"
    )

    def setUp(self):
        super().setUp()
        self.ev = self.p("Ren", "Everything")
        os.makedirs(os.path.join(self.ev, "260005-nry", "deep", "er"))
        self.sidecar = "260005-nry_fire_drill_@ai.md"
        open(os.path.join(self.ev, self.sidecar), "w").close()
        open(os.path.join(self.ev, "260004-nry_other.md"), "w").close()
        os.mkdir(os.path.join(self.ev, "260004-nry"))
        self.stub = self.p("stub.py")
        self.queue = self.p("queue")
        with open(self.stub, "w") as f:
            f.write(self.STUB)

    def run_rename(self, *edits, cwd=None):
        with open(self.queue, "w") as f:
            f.write("".join(e + "\n" for e in edits))
        cwd = cwd or os.path.join(self.ev, "260005-nry")
        env = {"EDITOR": f"{sys.executable} {self.stub} {self.queue}", "PWD": cwd}
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(cwd)
        try:
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = cli.main(["rename"])
        finally:
            os.chdir(old)
        return rc, out.getvalue(), err.getvalue()

    def names(self):
        return sorted(n for n in os.listdir(self.ev) if n.endswith(".md"))

    def seen(self):
        with open(self.queue + ".seen") as f:
            return f.read()

    def test_renames_and_shows_editable_part_only(self):
        rc, out, err = self.run_rename("fire_drill_v2_@ai_@x")
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(err, "everything rename: '260005-nry_fire_drill_@ai.md' -> "
                              "'260005-nry_fire_drill_v2_@ai_@x.md'\n")
        self.assertEqual(self.names(), ["260004-nry_other.md",
                                        "260005-nry_fire_drill_v2_@ai_@x.md"])
        self.assertTrue(self.seen().startswith("fire_drill_@ai\n# "))
        self.assertTrue(os.path.isdir(os.path.join(self.ev, "260005-nry")))

    def test_works_from_a_subdir_and_keeps_extension(self):
        os.rename(os.path.join(self.ev, self.sidecar),
                  os.path.join(self.ev, "260005-nry_fire_drill_@ai.txt"))
        rc, _, _ = self.run_rename("Fire_Drill_2", cwd=os.path.join(self.ev, "260005-nry", "deep", "er"))
        self.assertEqual(rc, 0)
        self.assertIn("260005-nry_Fire_Drill_2.txt", os.listdir(self.ev))

    def test_symlinked_entry_dir(self):
        os.symlink(self.p("elsewhere"), os.path.join(self.ev, "260006-lnk"))
        os.makedirs(self.p("elsewhere"), exist_ok=True)
        open(os.path.join(self.ev, "260006-lnk_linked.md"), "w").close()
        rc, _, _ = self.run_rename("linked_two", cwd=os.path.join(self.ev, "260006-lnk"))
        self.assertEqual(rc, 0)
        self.assertIn("260006-lnk_linked_two.md", os.listdir(self.ev))

    def assert_untouched(self):
        self.assertEqual([n for n in self.names() if os.path.isfile(os.path.join(self.ev, n))],
                         ["260004-nry_other.md", self.sidecar])

    def test_not_inside_an_entry(self):
        for cwd in (self.ev, self.home):
            rc, _, err = self.run_rename("x", cwd=cwd)
            self.assertEqual(rc, 1)
            self.assertIn("not inside an entry dir", err)
        self.assert_untouched()

    def test_entry_without_exactly_one_sidecar(self):
        os.mkdir(os.path.join(self.ev, "260007-nry"))  # no sidecar
        rc, _, err = self.run_rename("x", cwd=os.path.join(self.ev, "260007-nry"))
        self.assertEqual((rc, "not inside an entry dir" in err), (1, True))
        open(os.path.join(self.ev, "260005-nry_second.md"), "w").close()
        rc, _, err = self.run_rename("x")
        self.assertEqual((rc, "not inside an entry dir" in err), (1, True))
        self.assertFalse(os.path.exists(self.queue + ".seen"))

    def test_cancelled_or_unchanged_renames_nothing(self):
        for edit in ("!FAIL", "", "fire_drill_@ai", "!KEEP"):
            rc, _, err = self.run_rename(edit)
            self.assertEqual(rc, 1, edit)
            self.assertIn("nothing renamed", err)
        self.assert_untouched()

    def test_rejections_reopen_editor_with_error(self):
        cases = [
            ("has space", "whitespace"), ("a/b", "'/'"), ("na\u00efve", "characters not allowed"),
            ("a.b", "'.'"), ("_lead", "leading, trailing or doubled _"),
            ("trail_", "leading, trailing or doubled _"), ("a__b", "leading, trailing or doubled _"),
            ("a_@", "malformed tag"), ("a_@@x", "malformed tag"), ("a_b@c", "malformed tag"),
            ("@ai_desc", "start with a description"), ("a_@ai_more", "put all tags last"),
            ("x" * 81, "81 characters, at most 80"), ("other", None),
        ]
        for text, message in cases[:-1]:
            # first edit is rejected, the second saves the same text again: cancelled
            rc, _, err = self.run_rename(text, "!KEEP")
            self.assertEqual(rc, 1, text)
            self.assertIn(f"# ERROR: ", self.seen(), text)
            self.assertIn(message, self.seen(), text)
            os.remove(self.queue + ".seen")
        self.assert_untouched()

    def test_correcting_after_an_error_renames(self):
        rc, _, err = self.run_rename("bad name", "good_name")
        self.assertEqual(rc, 0)
        seen = self.seen().split("----\n")
        self.assertTrue(seen[1].startswith("bad name\n# ERROR: the name contains whitespace; "))
        self.assertIn("260005-nry_good_name.md", os.listdir(self.ev))

    def test_max_length_is_accepted(self):
        rc, _, _ = self.run_rename("x" * 80)
        self.assertEqual(rc, 0)
        self.assertIn("260005-nry_" + "x" * 80 + ".md", os.listdir(self.ev))

    def test_existing_target_is_rejected_and_never_overwritten(self):
        # a dir (or dangling symlink) with that name isn't a sidecar, but blocks the name
        os.mkdir(os.path.join(self.ev, "260005-nry_taken.md"))
        rc, _, _ = self.run_rename("taken", "!FAIL")
        self.assertEqual(rc, 1)
        self.assertIn("'260005-nry_taken.md' already exists", self.seen())
        self.assertIn(self.sidecar, os.listdir(self.ev))

    def test_rename_refuses_existing_target_directly(self):
        target = rename.find_target(os.path.join(self.ev, "260005-nry"))
        open(os.path.join(self.ev, "260005-nry_taken.md"), "w").close()
        with self.assertRaises(rename.Refusal):
            rename.rename(target, "260005-nry_taken.md")

    def test_result_passes_everything_check(self):
        self.run_rename("some_new_words_@ai_@y")
        found = check.check([self.ev])[0]
        self.assertNotIn("260005-nry", " ".join(f.name for f in found.findings))


GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV})


class EntryInfoTest(TreeTest):
    def setUp(self):
        super().setUp()
        self.ev = self.p("Inf", "Everything")
        self.entry = os.path.join(self.ev, "260005-nry")
        os.makedirs(os.path.join(self.entry, "sub", "deeper"))
        open(os.path.join(self.ev, "260005-nry_fire_drill_@ai_@x.md"), "w").close()

    def write(self, rel, data=b"", mtime=None):
        path = os.path.join(self.entry, rel)
        with open(path, "wb") as f:
            f.write(data)
        if mtime:
            os.utime(path, (mtime, mtime))
        return path

    def test_counts_size_and_newest(self):
        self.write("a.txt", b"x" * 100, mtime=1_700_000_000)
        self.write("sub/b.txt", b"y" * 20, mtime=1_700_000_500)
        ei = info.entry_info(self.entry)
        self.assertEqual((ei.entry.name, ei.files, ei.dirs, ei.symlinks), ("260005-nry", 2, 2, 0))
        self.assertEqual(ei.size, entries.tree_size(self.entry))
        self.assertGreaterEqual(ei.size, 120)
        self.assertEqual(ei.newest, 1_700_000_500)
        self.assertEqual([s.name for s in ei.sidecars], ["260005-nry_fire_drill_@ai_@x.md"])
        self.assertEqual((ei.sidecars[0].description, ei.sidecars[0].tags), ("fire_drill", ("ai", "x")))
        self.assertEqual((ei.git_repos, ei.warnings), ((), ()))

    def test_empty_entry_uses_dir_mtime(self):
        os.utime(self.entry, (1_600_000_000, 1_600_000_000))
        self.assertEqual(info.entry_info(self.entry).newest, 1_600_000_000)

    def test_symlinks_counted_never_followed(self):
        big = self.p("outside")
        os.makedirs(big)
        open(os.path.join(big, "huge.bin"), "wb").write(b"z" * 5000)
        os.symlink(big, os.path.join(self.entry, "dirlink"))
        os.symlink(os.path.join(big, "huge.bin"), os.path.join(self.entry, "filelink"))
        os.symlink(self.p("gone"), os.path.join(self.entry, "broken"))
        ei = info.entry_info(self.entry)
        self.assertEqual((ei.symlinks, ei.files, ei.dirs), (3, 0, 2))
        self.assertLess(ei.size, 5000)
        self.assertTrue(any("3 symlink(s)" in w for w in ei.warnings), ei.warnings)

    def test_no_sidecar_and_bad_name(self):
        os.remove(os.path.join(self.ev, "260005-nry_fire_drill_@ai_@x.md"))
        self.assertEqual(info.entry_info(self.entry).sidecars, ())
        with self.assertRaises(ValueError):
            info.entry_info(self.p("Inf"))

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_git_repo_states(self):
        repo = os.path.join(self.entry, "sub", "proj")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "main")
        self.write("sub/proj/f.txt", b"1")
        git(repo, "add", "f.txt")
        git(repo, "commit", "-q", "-m", "one")
        ei = info.entry_info(self.entry)
        self.assertEqual(ei.git_repos, (os.path.join("sub", "proj"),))
        self.assertEqual(ei.warnings, ("git repo 'sub/proj': 1 commit(s) not on any remote",))
        bare = self.p("remote.git")
        git(self.home, "init", "-q", "--bare", "-b", "main", bare)
        git(repo, "remote", "add", "origin", bare)
        git(repo, "push", "-q", "origin", "main")
        self.assertEqual(info.entry_info(self.entry).warnings, ())
        self.write("sub/proj/f.txt", b"2")
        self.write("sub/proj/new.txt", b"3")
        self.assertEqual(info.entry_info(self.entry).warnings,
                         ("git repo 'sub/proj': 2 uncommitted change(s)",))

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_entry_that_is_itself_a_repo(self):
        git(self.entry, "init", "-q", "-b", "main")
        ei = info.entry_info(self.entry)
        self.assertEqual(ei.git_repos, (".",))
        self.assertEqual(ei.warnings, ())  # no commits, nothing to lose

    def test_unreadable_git_state_warns(self):
        os.makedirs(os.path.join(self.entry, "r", ".git"))  # not a real repo
        ei = info.entry_info(self.entry)
        self.assertTrue(any("state could not be read" in w for w in ei.warnings), ei.warnings)


class RemoveCommandTest(TreeTest):
    def setUp(self):
        super().setUp()
        self.ev = self.p("Rem", "Everything")
        self.entry = os.path.join(self.ev, "260005-nry")
        os.makedirs(os.path.join(self.entry, "sub"))
        open(os.path.join(self.entry, "sub", "data.txt"), "w").write("hello")
        self.sidecar = os.path.join(self.ev, "260005-nry_fire_drill_@ai.md")
        open(self.sidecar, "w").close()
        self.other = os.path.join(self.ev, "260004-nry")
        os.makedirs(self.other)
        open(os.path.join(self.ev, "260004-nry_other.md"), "w").close()
        self.trash = self.p("trashdir")
        os.makedirs(self.trash)
        # a stand-in for trash-put that moves its arguments into self.trash
        self.stub_bin = self.p("stubbin")
        os.makedirs(self.stub_bin)
        stub = os.path.join(self.stub_bin, "trash-put")
        with open(stub, "w") as f:
            f.write(f'#!/bin/sh\n[ "$1" = -- ] && shift\nmv "$@" "{self.trash}"\n')
        os.chmod(stub, 0o755)

    def run_remove(self, stdin="", tty=True, cwd=None, tools=()):
        """Run `remove` in `cwd`; `tools` are the trash tools that exist (stub trash-put)."""
        cwd = cwd or self.entry
        real_which = shutil.which

        def which(name, *a, **k):
            if name == "trash-put" and "trash-put" in tools:
                return os.path.join(self.stub_bin, "trash-put")
            if name == "gio" and "gio" in tools:
                return "/usr/bin/gio"
            return None if name in ("gio", "trash-put") else real_which(name, *a, **k)
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(cwd)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    mock.patch.object(sys, "stdin", (Tty if tty else io.StringIO)(stdin)), \
                    mock.patch.dict(os.environ, {"PWD": cwd, "PATH": self.stub_bin + os.pathsep
                                                 + os.environ["PATH"]}), \
                    mock.patch("shutil.which", side_effect=which):
                rc = cli.main(["remove"])
        finally:
            os.chdir(old)
        return rc, out.getvalue(), err.getvalue()

    def assert_untouched(self):
        for p in (self.entry, self.sidecar, self.other):
            self.assertTrue(os.path.lexists(p), p)
        self.assertEqual(os.listdir(self.trash), [])

    def test_permanent_delete_removes_dir_and_sidecar(self):
        rc, out, err = self.run_remove("fire\n")
        self.assertEqual((rc, out), (0, self.ev + "\n"))
        self.assertFalse(os.path.lexists(self.entry))
        self.assertFalse(os.path.lexists(self.sidecar))
        self.assertTrue(os.path.isdir(self.other))
        self.assertTrue(os.path.isfile(os.path.join(self.ev, "260004-nry_other.md")))
        self.assertIn(">>> Will be PERMANENTLY DELETED (no trash tool found) <<<", err)
        self.assertIn("Type fire (from the description) to delete this entry and its sidecar", err)
        self.assertTrue(err.endswith("everything remove: deleted: 260005-nry/, "
                                     "260005-nry_fire_drill_@ai.md\n"))
        self.assertEqual(os.listdir(self.trash), [])

    def test_summary_is_shown_before_asking(self):
        rc, _, err = self.run_remove("\n")
        self.assertEqual(rc, 1)
        for text in ("Entry:        260005-nry", "Sidecar:      260005-nry_fire_drill_@ai.md",
                     "Description:  fire_drill", "Tags:         @ai",
                     "(1 file, 1 dir, 0 symlinks)", "Last changed: 20", "Location:     ~/Rem"):
            self.assertIn(text, err)
        self.assertLess(err.index("Entry:"), err.index("Type fire"))

    def test_trash_moves_both_together(self):
        rc, out, err = self.run_remove("fire\n", tools=("trash-put",))
        self.assertEqual((rc, out), (0, self.ev + "\n"))
        self.assertIn(">>> Will be MOVED TO THE TRASH (restorable) <<<", err)
        self.assertTrue(err.endswith("moved to the trash: 260005-nry/, 260005-nry_fire_drill_@ai.md\n"))
        self.assertEqual(sorted(os.listdir(self.trash)),
                         ["260005-nry", "260005-nry_fire_drill_@ai.md"])
        self.assertFalse(os.path.lexists(self.entry))
        self.assertFalse(os.path.lexists(self.sidecar))

    def test_gio_is_preferred_over_trash_put(self):
        with mock.patch("shutil.which", side_effect=lambda n: "/x/" + n):
            self.assertEqual(remove.choose_method().trash_cmd, ["gio", "trash", "--"])
        with mock.patch("shutil.which", side_effect=lambda n: "/x/" + n if n == "trash-put" else None):
            self.assertEqual(remove.choose_method().trash_cmd, ["trash-put", "--"])
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(remove.choose_method().trash)

    def test_failing_trash_tool_reports_what_is_left(self):
        with open(os.path.join(self.stub_bin, "trash-put"), "w") as f:
            f.write("#!/bin/sh\necho nope >&2\nexit 1\n")
        rc, out, err = self.run_remove("fire\n", tools=("trash-put",))
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("trash-put failed (nope); removed: nothing; still there: 260005-nry/, "
                      "260005-nry_fire_drill_@ai.md", err)
        self.assert_untouched()

    def test_wrong_confirmation_deletes_nothing(self):
        for answer in ("", "\n", "5", "yes", "0004", "DELETE", "260005", " 0006 ",
                       "0005", "fir", "fire drill", "drill", "fire_drill", "fire x"):
            rc, out, err = self.run_remove(answer + "\n")
            self.assertEqual((rc, out), (1, ""), answer)
            self.assertTrue(err.endswith("everything remove: not confirmed - nothing deleted\n"))
            self.assert_untouched()

    def test_confirmation_ignores_surrounding_space_only(self):
        rc, _, _ = self.run_remove("  fire  \n")
        self.assertEqual(rc, 0)

    def test_confirmation_is_case_insensitive(self):
        rc, _, _ = self.run_remove("FiRe\n")
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.lexists(self.entry))

    def test_sequence_number_no_longer_confirms(self):
        rc, out, err = self.run_remove("0005\n")
        self.assertEqual((rc, out), (1, ""))
        self.assertTrue(err.endswith("not confirmed - nothing deleted\n"))
        self.assert_untouched()

    def test_prompt_shows_the_word_as_written_in_the_sidecar(self):
        os.rename(self.sidecar, os.path.join(self.ev, "260005-nry_Fire_drill_@ai.md"))
        self.sidecar = os.path.join(self.ev, "260005-nry_Fire_drill_@ai.md")
        rc, out, err = self.run_remove("fire\n")
        self.assertEqual(rc, 0)
        self.assertIn("Type Fire (from the description)", err)

    def test_short_first_word_uses_first_six_characters(self):
        os.rename(self.sidecar, os.path.join(self.ev, "260005-nry_go_to_the_store.md"))
        self.sidecar = os.path.join(self.ev, "260005-nry_go_to_the_store.md")
        rc, out, err = self.run_remove("go\n")
        self.assertEqual(rc, 1)
        self.assertIn("Type go_to_ (from the description)", err)
        rc, out, err = self.run_remove("GO_TO_\n")
        self.assertEqual(rc, 0)

    def test_description_shorter_than_six_uses_all_of_it(self):
        os.rename(self.sidecar, os.path.join(self.ev, "260005-nry_ab.md"))
        self.sidecar = os.path.join(self.ev, "260005-nry_ab.md")
        rc, out, err = self.run_remove("ab\n")
        self.assertEqual(rc, 0)
        self.assertIn("Type ab (from the description)", err)

    def test_confirm_word_helper(self):
        w = remove.confirm_word
        self.assertEqual(w("fire_drill", "0005"), "fire")
        self.assertEqual(w("Fire_drill", "0005"), "Fire")
        self.assertEqual(w("backup", "0005"), "backup")
        self.assertEqual(w("go_to_the_store", "0005"), "go_to_")   # first word < 3 chars
        self.assertEqual(w("a_longer_one", "0005"), "a_long")
        self.assertEqual(w("--_fix_it", "0005"), "--_fix")          # only symbols
        self.assertEqual(w("x-y_rest", "0005"), "x-y_re")           # < 3 letters or digits
        self.assertEqual(w("ab", "0005"), "ab")                     # fewer than 6 chars
        self.assertEqual(w("a_b", "0005"), "a_b")
        self.assertEqual(w("e-mail_setup", "0005"), "e-mail")
        self.assertEqual(w("", "0005"), "0005")                     # nothing to type
        self.assertEqual(w("  ", "0005"), "0005")

    def test_without_terminal_refuses(self):
        rc, out, err = self.run_remove("fire\n", tty=False)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("needs a terminal to ask on, refusing - nothing deleted", err)
        self.assert_untouched()

    def test_from_a_subdir_deletes_the_whole_entry(self):
        rc, out, _ = self.run_remove("fire\n", cwd=os.path.join(self.entry, "sub"))
        self.assertEqual((rc, out), (0, self.ev + "\n"))
        self.assertFalse(os.path.lexists(self.entry))

    def test_not_inside_an_entry(self):
        for cwd in (self.ev, self.home):
            rc, out, err = self.run_remove("fire\n", cwd=cwd)
            self.assertEqual((rc, out), (1, ""))
            self.assertIn("not inside an entry dir - nothing deleted", err)
        self.assert_untouched()

    def test_no_sidecar_or_several_refuse(self):
        os.remove(self.sidecar)
        rc, _, err = self.run_remove("fire\n")
        self.assertEqual(rc, 1)
        self.assertIn("'260005-nry' has no sidecar file next to it", err)
        self.assertTrue(os.path.isdir(self.entry))
        for name in ("260005-nry_a.md", "260005-nry_b.md"):
            open(os.path.join(self.ev, name), "w").close()
        rc, _, err = self.run_remove("fire\n")
        self.assertEqual(rc, 1)
        self.assertIn("'260005-nry' has 2 sidecar files (260005-nry_a.md, 260005-nry_b.md)", err)
        self.assertTrue(os.path.isdir(self.entry))

    def test_never_deletes_an_outer_entry_instead(self):
        inner = os.path.join(self.entry, "sub", "250001-sub")  # entry-like dir, no sidecar
        os.makedirs(inner)
        rc, out, err = self.run_remove("fire\n", cwd=inner)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("'250001-sub' has no sidecar", err)
        self.assert_untouched()
        self.assertTrue(os.path.isdir(inner))

    def test_symlinked_entry_is_refused(self):
        real = self.p("elsewhere", "real")
        os.makedirs(real)
        link = os.path.join(self.ev, "260006-lnk")
        os.symlink(real, link)
        open(os.path.join(self.ev, "260006-lnk_linked.md"), "w").close()
        rc, out, err = self.run_remove("linked\n", cwd=link)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("'260006-lnk' is a symlink, refusing to delete through it", err)
        self.assertTrue(os.path.isdir(real))
        self.assertTrue(os.path.islink(link))

    def test_mount_point_is_refused(self):
        with mock.patch("os.path.ismount", side_effect=lambda p: p == self.entry):
            rc, out, err = self.run_remove("fire\n")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("'260005-nry' is a mount point, refusing", err)
        self.assert_untouched()

    def test_mount_point_inside_is_refused(self):
        real = info.entry_info
        with mock.patch.object(info, "entry_info", side_effect=lambda p: __import__(
                "dataclasses").replace(real(p), mount_points=("sub",))):
            rc, out, err = self.run_remove("fire\n")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("'sub' inside the entry is another filesystem", err)
        self.assert_untouched()

    def test_failure_says_what_is_gone_and_what_is_left(self):
        real_unlink = os.unlink

        def unlink(path, *a, **k):
            if path == self.sidecar:
                raise PermissionError(13, "Permission denied")
            return real_unlink(path, *a, **k)
        with mock.patch("os.unlink", side_effect=unlink):
            rc, out, err = self.run_remove("fire\n")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("Permission denied: removed: 260005-nry/; still there: "
                      "260005-nry_fire_drill_@ai.md", err)
        self.assertFalse(os.path.lexists(self.entry))
        self.assertTrue(os.path.lexists(self.sidecar))

    def test_deleting_does_not_follow_symlinks(self):
        outside = self.p("outside")
        os.makedirs(outside)
        open(os.path.join(outside, "keep.txt"), "w").close()
        os.symlink(outside, os.path.join(self.entry, "link"))
        rc, _, err = self.run_remove("fire\n")
        self.assertEqual(rc, 0)
        self.assertIn("1 symlink", err)
        self.assertTrue(os.path.isfile(os.path.join(outside, "keep.txt")))

    def test_warnings_are_shown_in_the_prompt(self):
        os.makedirs(os.path.join(self.entry, "r", ".git"))
        _, _, err = self.run_remove("\n")
        self.assertIn("  WARNING: git repo 'r': state could not be read", err)


    def test_everything_function_cds_after_remove_and_passes_the_rest_through(self):
        functions = os.path.join(REPO, "modules", "base", ".config", "dotfiles",
                                 "functions.d", "base.sh")
        fake = os.path.join(self.stub_bin, "everything")  # the command on PATH
        with open(fake, "w") as f:
            f.write(f'#!/bin/sh\nif [ "$1" = remove ]; then echo "{self.ev}"; '
                    'else echo "args: $*"; fi\n')
        os.chmod(fake, 0o755)
        env = {**os.environ, "HOME": self.home, "PATH": self.stub_bin + os.pathsep + os.environ["PATH"]}

        def run(cmd):
            return subprocess.run(["bash", "-c", f'source "{functions}"; {cmd}'],
                                  capture_output=True, text=True, cwd=self.entry, env=env)
        res = run("everything remove --label x; echo rc=$?; pwd")
        self.assertEqual(res.stdout.splitlines(), ["rc=0", self.ev], res.stderr)
        res = run("everything list --paths; pwd")
        self.assertEqual(res.stdout.splitlines(), ["args: list --paths", self.entry])
        # a failing remove leaves the shell where it is
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        res = run("everything remove; echo rc=$?; pwd")
        self.assertEqual(res.stdout.splitlines(), ["rc=1", self.entry])


class InfoCommandTest(TreeTest):
    def setUp(self):
        super().setUp()
        self.main = self.p("Main", "Everything")
        self.entry = os.path.join(self.main, "250002-nry")
        os.makedirs(os.path.join(self.entry, "sub"))
        with open(os.path.join(self.entry, "sub", "data.txt"), "w") as f:
            f.write("hello")

    def run_info(self, *args, cwd=None, fzf=None, tty=False):
        """Run `info` in `cwd`; `fzf` is None (not installed) or a function(rows) -> picked row."""
        cwd = cwd or self.home
        calls = []

        def fake_run(cmd, input, **kw):
            calls.append(input.splitlines())
            row = fzf(input.splitlines())
            return subprocess.CompletedProcess(cmd, 0 if row else 130,
                                               stdout=(row + "\n") if row else "")
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(cwd)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    mock.patch.dict(os.environ, {"PWD": cwd}), \
                    mock.patch.object(sys, "stdin", io.StringIO("")), \
                    mock.patch("shutil.which", return_value="/usr/bin/fzf" if fzf else None), \
                    mock.patch("subprocess.run", side_effect=fake_run) if fzf else \
                    contextlib.nullcontext():
                rc = cli.main(["info", *args])
        finally:
            os.chdir(old)
        self.calls = calls
        return rc, out.getvalue(), err.getvalue()

    def snapshot(self):
        return sorted((os.path.join(d, n), os.lstat(os.path.join(d, n)).st_mtime_ns)
                      for d, dn, fn in os.walk(self.home) for n in dn + fn)

    def test_inside_an_entry_prints_the_summary(self):
        before = self.snapshot()
        rc, out, err = self.run_info(cwd=os.path.join(self.entry, "sub"))
        self.assertEqual((rc, err), (0, ""))
        for text in ("Entry:        250002-nry",
                     "Sidecar:      250002-nry_first_note_@ai_@x.md",
                     "Description:  first_note", "Tags:         @ai @x",
                     "(1 file, 1 dir, 0 symlinks)", "Last changed: 20", "Location:     ~/Main/"):
            self.assertIn(text, out)
        self.assertNotIn("Type ", out + err)  # no delete prompt
        self.assertEqual(self.snapshot(), before)

    def test_shows_warnings(self):
        os.makedirs(os.path.join(self.entry, "r", ".git"))
        _, out, _ = self.run_info(cwd=self.entry)
        self.assertIn("  WARNING: git repo 'r': state could not be read", out)

    def test_symlinked_entry_is_fine_read_only(self):
        open(os.path.join(self.main, "260002-lnk_linked.md"), "w").close()
        link = os.path.join(self.main, "260002-lnk")
        rc, out, _ = self.run_info(cwd=link)
        self.assertEqual(rc, 0)
        self.assertIn("Entry:        260002-lnk", out)

    def test_entry_without_sidecar_is_reported_not_picked(self):
        rc, out, err = self.run_info(cwd=os.path.join(self.main, "250001"),
                                     fzf=lambda rows: self.fail("picker must not open"))
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("everything info: '250001' has no sidecar file next to it", err)
        self.assertNotIn("nothing deleted", err)

    def test_picker_default_scope_is_the_bashmark_dir(self):
        self.save(self.main, self.p("Archive", "old_everything"))
        other = self.p("Other-Everything")
        os.makedirs(os.path.join(other, "260009-zzz"))
        self.save(self.main, other)
        rc, out, err = self.run_info(fzf=lambda rows: next(r for r in rows if "250002-nry" in r))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("Entry:        250002-nry", out)
        rows = self.calls[0]
        self.assertTrue(rows)
        self.assertTrue(all(r.split("\t")[1].startswith(self.main + "/") for r in rows), rows)
        self.assertFalse(any("260009" in r for r in rows))
        self.assertIn("250002-nry  first_note  @ai @x\t" + self.entry, rows)

    def test_picker_all_scope_uses_saved_dirs(self):
        other = self.p("Other-Everything")
        os.makedirs(os.path.join(other, "260009-zzz"))
        open(os.path.join(other, "260009-zzz_far_away.md"), "w").close()
        self.save(self.main, other)
        rc, out, _ = self.run_info("--all", fzf=lambda rows: next(r for r in rows if "260009" in r))
        self.assertEqual(rc, 0)
        self.assertIn("Entry:        260009-zzz", out)
        self.assertIn("Description:  far_away", out)
        rows = self.calls[0]
        self.assertIn("260009-zzz  far_away  [~/Other-Everything]\t" + os.path.join(other, "260009-zzz"), rows)
        self.assertTrue(any("250002-nry" in r for r in rows))

    def test_cancelled_picker_prints_nothing(self):
        rc, out, err = self.run_info(fzf=lambda rows: None)
        self.assertEqual((rc, out, err), (1, "", ""))

    def test_no_fzf_gives_a_clear_message(self):
        rc, out, err = self.run_info()
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("not inside an entry dir and fzf not found to pick one", err)

    def test_no_bookmark_and_not_set_up(self):
        os.remove(self.p(".sdirs"))
        rc, _, err = self.run_info(fzf=lambda rows: rows[0])
        self.assertEqual(rc, 1)
        self.assertIn("bashmark 'e' is not set to a valid dir", err)
        os.remove(saved_locations.list_file(self.home))
        rc, _, err = self.run_info("-a", fzf=lambda rows: rows[0])
        self.assertEqual(rc, 1)
        self.assertIn("no list of Everything dirs yet", err)

    def test_picker_modifies_nothing(self):
        before = self.snapshot()
        self.run_info(fzf=lambda rows: rows[-1])
        self.run_info("-a", fzf=lambda rows: rows[0])
        self.assertEqual(self.snapshot(), before)


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
    """Saved symlinked Everything dirs count once per real dir (tickets 019, 024)."""
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
        self.save(self.p("Main", "Everything"),
                  self.p("Main", "Everything", "250001", "sub-everything"),
                  self.link, self.p("Main", "zz-second-everything"),
                  self.p("Main", "dup-everything"))

    def test_drop_duplicate_links(self):
        dirs = discovery.find_everything_dirs(self.root, self.home, links=True)
        self.assertEqual(discovery.drop_duplicate_links(dirs), [
            self.p("Main", "Everything"),
            self.p("Main", "Everything", "250001", "sub-everything"),
            self.link,
        ])

    def test_goto_all_finds_entry_via_link(self):
        rc, out, err = self.run_cli("goto", "--all", "50", "26")
        self.assertEqual((rc, out), (0, os.path.join(self.link, "260050-ext") + "\n"))
        self.assertEqual(err, "everything goto: 260050-ext_external_note_@ai  ~/Main/ext-everything\n")

    def test_goto_all_no_duplicates_via_link_to_found_dir(self):
        # 250002-nry is in ~/Main/Everything, also reachable as dup-everything
        rc, out, _ = self.run_cli("goto", "--all", "2", "25")
        self.assertEqual((rc, out), (0, self.p("Main", "Everything", "250002-nry") + "\n"))

    def test_entries_all(self):
        rc, out, _ = self.run_cli("entries", "--all")
        self.assertEqual(rc, 0)
        self.assertIn("(8 entries in 3 dirs)", out)
        self.assertEqual(out.count("260050-ext"), 1)
        self.assertIn("~/Main/ext-everything", out)

    def test_stats_and_check_count_link_once(self):
        _, out, _ = self.run_cli("stats", "--json")
        data = json.loads(out)
        self.assertEqual(data["entries"], 8)
        _, out, _ = self.run_cli("check", "--json")
        self.assertEqual([d["dir"] for d in json.loads(out)["dirs"]], [
            self.p("Main", "Everything"),
            self.p("Main", "Everything", "250001", "sub-everything"),
            self.link,
        ])

    def test_help_documents_the_saved_list(self):
        for cmd in ["goto", "entries", "stats", "check", "pick"]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                cli.main([cmd, "--help"])
            text = " ".join(out.getvalue().split())
            self.assertIn("saved list of Everything dirs", text, cmd)
            for gone in ("--root", "--fresh", "--network"):
                self.assertNotIn(gone, text, cmd)


if __name__ == "__main__":
    unittest.main()
