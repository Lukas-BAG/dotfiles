"""Tests for the evtools package (the `everything` command).

Run from the repo root:  python3 -m unittest discover tests/evtools
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "modules", "base", ".config", "dotfiles"))

from evtools import cli, discovery, entries, stats  # noqa: E402

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
        self.assertEqual(st.shared_ids, {250001: ["(none)", "sub"], 260001: ["abc", "gel"]})
        self.assertEqual(len(st.ids), 5)
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
  distinct ids    5   (2 ids are used by several suffixes)
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
        self.assertEqual(out.count("Everything stats for "), 4)
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
        self.assertEqual(data["shared_ids"], {"250001": ["(none)", "sub"],
                                              "260001": ["abc", "gel"]})
        self.assertEqual(data["by_year"], {"2025": 4, "2026": 3})
        self.assertEqual(data["suffixes"]["nry"], {"entries": 2,
                                                   "ranges": {"2025": ["0002", "0003"]}})
        self.assertEqual(data["tags"], {"ai": 2, "x": 1, "y": 1})
        _, out, _ = self.run_cli("stats", "--json", "--per-dir")
        self.assertEqual([len(d["dirs"]) for d in json.loads(out)], [1, 1, 1, 1])

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

    def test_separate_entry_per_root(self):
        self.find()
        dirs, age = self.find(self.p("Archive"))
        self.assertIsNone(age)
        self.assertEqual(dirs, [self.p("Archive", "old_everything")])
        self.assertEqual(len(self.find()[0]), 4)

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


if __name__ == "__main__":
    unittest.main()
