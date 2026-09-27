"""Tests for sync_claude_settings.py.

Run from the repo root:  python3 -m unittest discover tests/claude_settings
"""

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

import sync_claude_settings as scs  # noqa: E402

SCRIPT = os.path.join(REPO, "sync_claude_settings.py")


class MergeTest(unittest.TestCase):
    """The pure merge functions, without any files."""

    def test_defaults_fill_missing_keys_only(self):
        live = {"model": "opus", "theme": "light"}
        added = scs.apply_defaults(live, {"model": "sonnet", "verbose": True})
        self.assertEqual(live, {"model": "opus", "theme": "light", "verbose": True})
        self.assertEqual(added, [(("verbose",), True)])

    def test_defaults_recurse_into_objects(self):
        live = {"modelSettings": {"claude-opus-5-5": {"effortLevel": "high"}}}
        scs.apply_defaults(live, {"modelSettings": {
            "claude-opus-5-5": {"effortLevel": "low", "extra": 1},
            "claude-sonnet-5": {"effortLevel": "medium"}}})
        self.assertEqual(live, {"modelSettings": {
            "claude-opus-5-5": {"effortLevel": "high", "extra": 1},
            "claude-sonnet-5": {"effortLevel": "medium"}}})

    def test_defaults_never_override_a_type_mismatch(self):
        live = {"modelSettings": None, "list": "not a list"}
        added = scs.apply_defaults(live, {"modelSettings": {"x": 1}, "list": [1]})
        self.assertEqual(live, {"modelSettings": None, "list": "not a list"})
        self.assertEqual(added, [])

    def test_defaults_list_is_not_combined(self):
        live = {"permissions": {"allow": ["Bash(ls)"]}}
        scs.apply_defaults(live, {"permissions": {"allow": ["Bash(git status)"]}})
        self.assertEqual(live["permissions"]["allow"], ["Bash(ls)"])

    def test_enforced_adds_missing_without_conflict(self):
        live = {}
        added, conflicts = scs.apply_enforced(live, {"theme": "dark", "hooks": {"Stop": []}})
        self.assertEqual(live, {"theme": "dark", "hooks": {"Stop": []}})
        self.assertEqual(conflicts, [])
        self.assertEqual({p for p, _ in added}, {("theme",), ("hooks",)})

    def test_enforced_equal_value_is_no_change(self):
        live = {"theme": "dark", "hooks": {"Stop": [{"a": 1}]}}
        added, conflicts = scs.apply_enforced(live, {"theme": "dark", "hooks": {"Stop": [{"a": 1}]}})
        self.assertEqual((added, conflicts), ([], []))

    def test_enforced_differing_value_is_a_conflict_not_applied(self):
        live = {"theme": "light"}
        added, conflicts = scs.apply_enforced(live, {"theme": "dark"})
        self.assertEqual(live, {"theme": "light"})
        self.assertEqual(conflicts, [(("theme",), "light", "dark")])

    def test_enforced_keeps_sibling_keys_set_locally(self):
        # An enforced object above values set only on this machine: those
        # values stay, only the enforced leaves are touched.
        live = {"hooks": {"PreToolUse": [{"local": True}], "Stop": [{"old": 1}]}}
        _, conflicts = scs.apply_enforced(live, {"hooks": {"Stop": [{"new": 1}]}})
        self.assertEqual(live["hooks"]["PreToolUse"], [{"local": True}])
        self.assertEqual([c[0] for c in conflicts], [("hooks", "Stop")])

    def test_enforced_leaf_below_a_local_object(self):
        live = {"a": {"b": {"local": 1}}}
        added, conflicts = scs.apply_enforced(live, {"a": {"b": {"c": 2}}})
        self.assertEqual(live, {"a": {"b": {"local": 1, "c": 2}}})
        self.assertEqual((added, conflicts), ([(("a", "b", "c"), 2)], []))

    def test_enforced_object_over_local_non_object_is_one_conflict(self):
        live = {"hooks": "oops"}
        _, conflicts = scs.apply_enforced(live, {"hooks": {"Stop": [], "Notification": []}})
        self.assertEqual(conflicts, [(("hooks",), "oops", {"Stop": [], "Notification": []})])
        self.assertEqual(live, {"hooks": "oops"})

    def test_enforced_non_object_over_local_object_is_a_conflict(self):
        live = {"statusLine": {"type": "command"}}
        _, conflicts = scs.apply_enforced(live, {"statusLine": None})
        self.assertEqual(conflicts, [(("statusLine",), {"type": "command"}, None)])

    def test_enforced_null_is_a_value(self):
        live = {"x": None}
        added, conflicts = scs.apply_enforced(live, {"x": None, "y": None})
        self.assertEqual(live, {"x": None, "y": None})
        self.assertEqual((added, conflicts), ([(("y",), None)], []))

    def test_enforced_list_is_compared_whole(self):
        # Extra local entry, different order, and a subset all conflict:
        # lists are never combined.
        for local in (["a", "b", "c"], ["b", "a"], ["a"], []):
            live = {"l": local}
            _, conflicts = scs.apply_enforced(live, {"l": ["a", "b"]})
            self.assertEqual(len(conflicts), 1, local)
            self.assertEqual(live["l"], local)

    def test_enforced_list_of_objects_compared_deeply(self):
        hook = [{"hooks": [{"type": "command", "command": "x"}]}]
        live = {"Stop": [{"hooks": [{"command": "x", "type": "command"}]}]}  # key order differs
        _, conflicts = scs.apply_enforced(live, {"Stop": hook})
        self.assertEqual(conflicts, [])

    def test_enforced_empty_object_ensures_object(self):
        live = {"a": {"k": 1}}
        added, conflicts = scs.apply_enforced(live, {"a": {}, "b": {}})
        self.assertEqual(live, {"a": {"k": 1}, "b": {}})
        self.assertEqual(conflicts, [])

    def test_json_equal_is_type_strict(self):
        self.assertFalse(scs.json_equal(True, 1))
        self.assertFalse(scs.json_equal(0, False))
        self.assertFalse(scs.json_equal("1", 1))
        self.assertFalse(scs.json_equal([True], [1]))
        self.assertFalse(scs.json_equal({"a": 1}, {"a": 1, "b": None}))
        self.assertTrue(scs.json_equal(1, 1.0))
        self.assertTrue(scs.json_equal({"a": [1, {"b": None}]}, {"a": [1, {"b": None}]}))

    def test_enforced_bool_vs_int_is_a_conflict(self):
        _, conflicts = scs.apply_enforced({"flag": 1}, {"flag": True})
        self.assertEqual(len(conflicts), 1)

    def test_overlap_between_files_is_an_error(self):
        for defaults, enforced in [
            ({"model": "a"}, {"model": "b"}),
            ({"hooks": []}, {"hooks": {"Stop": []}}),       # default above enforced
            ({"a": {"b": {"c": 1}}}, {"a": {"b": 2}}),      # enforced above default
            ({"a": {}}, {"a": {"b": 1}}),
        ]:
            with self.assertRaises(scs.SyncError, msg=(defaults, enforced)):
                scs.check_no_overlap(defaults, enforced)

    def test_siblings_in_both_files_are_fine(self):
        scs.check_no_overlap({"modelSettings": {"a": {"effortLevel": "low"}}},
                             {"modelSettings": {"b": {"effortLevel": "high"}}})


class SyncTest(unittest.TestCase):
    """The whole run against temp files."""

    DEFAULTS = {"model": "sonnet", "modelSettings": {"claude-sonnet-5": {"effortLevel": "medium"}}}
    ENFORCED = {"theme": "dark", "remoteControlAtStartup": False,
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify"}]}]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = os.path.realpath(self.tmp.name)
        self.tracked = os.path.join(root, "tracked")
        self.backups = os.path.join(root, "repo", "backups")
        self.home = os.path.join(root, "home")
        self.live = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(self.tracked)
        os.makedirs(self.home)
        self.write_tracked(self.DEFAULTS, self.ENFORCED)
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old_home)
                        if old_home is not None else os.environ.pop("HOME", None))

    def write_tracked(self, defaults, enforced):
        for name, data in [(scs.DEFAULTS_FILE, defaults), (scs.ENFORCED_FILE, enforced)]:
            with open(os.path.join(self.tracked, name), "w") as f:
                json.dump(data, f)

    def write_live(self, data):
        os.makedirs(os.path.dirname(self.live), exist_ok=True)
        with open(self.live, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data, indent=2))

    def read_live(self):
        with open(self.live) as f:
            return json.load(f)

    def run_sync(self, answers=(), interactive=True, dry_run=False):
        answers = list(answers)
        prompts = []

        def fake_input(prompt):
            prompts.append(prompt)
            if not answers:
                raise AssertionError("asked more questions than expected")
            return answers.pop(0)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            scs.sync(self.live, tracked_dir=self.tracked, backups_dir=self.backups,
                     dry_run=dry_run, interactive=interactive, input_fn=fake_input)
        self.assertEqual(answers, [], "not all answers were used")
        return out.getvalue(), prompts

    def backup_files(self):
        if not os.path.isdir(self.backups):
            return []
        return sorted(os.path.join(d, f) for d, _, files in os.walk(self.backups) for f in files)

    def test_creates_missing_dir_and_file(self):
        out, _ = self.run_sync()
        self.assertIn("does not exist", out)
        self.assertEqual(self.read_live(), {**self.DEFAULTS, **self.ENFORCED})
        self.assertEqual(self.backup_files(), [])

    def test_second_run_changes_nothing(self):
        self.write_live({"model": "opus", "theme": "light", "permissions": {"allow": ["x"]}})
        self.run_sync(answers=["y"])
        with open(self.live) as f:
            first = f.read()
        mtime = os.stat(self.live).st_mtime_ns
        out, prompts = self.run_sync()
        self.assertEqual(prompts, [])
        self.assertIn("Already in sync", out)
        with open(self.live) as f:
            self.assertEqual(f.read(), first)
        self.assertEqual(os.stat(self.live).st_mtime_ns, mtime)
        self.assertEqual(len(self.backup_files()), 1)

    def test_keeps_unmanaged_keys_and_local_default_choice(self):
        self.write_live({"model": "opus", "permissions": {"allow": ["Bash(ls)"]},
                         "hooks": {"PreToolUse": [{"local": 1}]}})
        self.run_sync()
        live = self.read_live()
        self.assertEqual(live["model"], "opus")
        self.assertEqual(live["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(live["hooks"]["PreToolUse"], [{"local": 1}])
        self.assertEqual(live["hooks"]["Stop"], self.ENFORCED["hooks"]["Stop"])
        self.assertEqual(live["modelSettings"], self.DEFAULTS["modelSettings"])

    def test_non_conflicting_applied_before_conflicts_are_asked(self):
        self.write_live({"theme": "light"})
        out, prompts = self.run_sync(answers=["n"])
        self.assertEqual(len(prompts), 1)
        live = self.read_live()
        self.assertEqual(live["theme"], "light")
        self.assertIs(live["remoteControlAtStartup"], False)
        self.assertIn("-\"light\"", out.replace(" ", ""))
        self.assertIn("Declined", out)

    def test_declined_conflict_is_asked_again(self):
        self.write_live({"theme": "light"})
        self.run_sync(answers=["n"])
        backups = len(self.backup_files())
        out, prompts = self.run_sync(answers=["n"])
        self.assertEqual(len(prompts), 1)
        self.assertEqual(len(self.backup_files()), backups)  # nothing written
        self.assertIn("Nothing written", out)

    def test_yes_applies_and_backs_up_original(self):
        original = {"theme": "light", "remoteControlAtStartup": True}
        self.write_live(original)
        self.run_sync(answers=["y", "y"])
        self.assertEqual(self.read_live()["theme"], "dark")
        self.assertIs(self.read_live()["remoteControlAtStartup"], False)
        [backup] = self.backup_files()
        self.assertEqual(os.path.basename(backup), scs.BACKUP_NAME)
        with open(backup) as f:
            self.assertEqual(json.load(f), original)

    def test_all_applies_remaining_without_asking(self):
        self.write_live({"theme": "light", "remoteControlAtStartup": True,
                         "hooks": {"Stop": []}})
        _, prompts = self.run_sync(answers=["n", "a"])
        self.assertEqual(len(prompts), 2)
        live = self.read_live()
        # Order follows enforced.json: theme (declined), remoteControl (all), hooks.
        self.assertEqual(live["theme"], "light")
        self.assertIs(live["remoteControlAtStartup"], False)
        self.assertEqual(live["hooks"]["Stop"], self.ENFORCED["hooks"]["Stop"])

    def test_invalid_answer_is_asked_again(self):
        self.write_live({"theme": "light"})
        _, prompts = self.run_sync(answers=["maybe", "", "Y"])
        self.assertEqual(len(prompts), 3)
        self.assertEqual(self.read_live()["theme"], "dark")

    def test_no_terminal_skips_conflicts_applies_the_rest(self):
        self.write_live({"theme": "light"})
        out, prompts = self.run_sync(interactive=False)
        self.assertEqual(prompts, [])
        live = self.read_live()
        self.assertEqual(live["theme"], "light")
        self.assertEqual(live["model"], "sonnet")
        self.assertIn("No terminal", out)

    def test_dry_run_writes_nothing(self):
        self.write_live({"theme": "light"})
        out, prompts = self.run_sync(dry_run=True)
        self.assertEqual(prompts, [])
        self.assertEqual(self.read_live(), {"theme": "light"})
        self.assertEqual(self.backup_files(), [])
        self.assertIn("Conflict (would ask): theme", out)
        self.assertIn("model", out)

    def test_dry_run_does_not_create_missing_file(self):
        self.run_sync(dry_run=True)
        self.assertFalse(os.path.lexists(self.live))
        self.assertFalse(os.path.lexists(os.path.dirname(self.live)))

    def test_refuses_symlinked_file(self):
        target = os.path.join(self.home, "repo_settings.json")
        with open(target, "w") as f:
            f.write('{"theme": "light"}')
        os.makedirs(os.path.dirname(self.live))
        os.symlink(target, self.live)
        with self.assertRaises(scs.SyncError):
            self.run_sync()
        with open(target) as f:
            self.assertEqual(f.read(), '{"theme": "light"}')
        self.assertTrue(os.path.islink(self.live))

    def test_refuses_dangling_symlink(self):
        os.makedirs(os.path.dirname(self.live))
        os.symlink(os.path.join(self.home, "gone.json"), self.live)
        with self.assertRaises(scs.SyncError):
            self.run_sync()
        self.assertFalse(os.path.exists(os.path.join(self.home, "gone.json")))

    def test_refuses_folded_parent_dir(self):
        real = os.path.join(self.home, "module", ".claude")
        os.makedirs(real)
        os.symlink(real, os.path.dirname(self.live))
        with self.assertRaises(scs.SyncError):
            self.run_sync()
        self.assertEqual(os.listdir(real), [])

    def test_invalid_live_json_is_an_error_and_untouched(self):
        self.write_live("{not json")
        with self.assertRaises(scs.SyncError):
            self.run_sync()
        with open(self.live) as f:
            self.assertEqual(f.read(), "{not json")

    def test_live_top_level_must_be_object(self):
        self.write_live("[]")
        with self.assertRaises(scs.SyncError):
            self.run_sync()

    def test_overlap_in_tracked_files_writes_nothing(self):
        self.write_tracked({"theme": "light"}, {"theme": "dark"})
        with self.assertRaises(scs.SyncError):
            self.run_sync()
        self.assertFalse(os.path.lexists(self.live))

    def test_keeps_key_order_and_file_mode(self):
        self.write_live({"zzz": 1, "theme": "light", "aaa": 2})
        os.chmod(self.live, 0o640)
        self.run_sync(answers=["y"])
        with open(self.live) as f:
            keys = list(json.load(f).keys())
        self.assertEqual(keys[:3], ["zzz", "theme", "aaa"])
        self.assertEqual(stat.S_IMODE(os.stat(self.live).st_mode), 0o640)
        self.assertEqual(os.listdir(os.path.dirname(self.live)), ["settings.json"])  # no temp left

    def test_abort_mid_prompt_writes_nothing(self):
        self.write_live({"theme": "light"})

        def eof(_prompt):
            raise EOFError

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(EOFError):
            scs.sync(self.live, tracked_dir=self.tracked, backups_dir=self.backups,
                     interactive=True, input_fn=eof)
        self.assertEqual(self.read_live(), {"theme": "light"})
        self.assertEqual(self.backup_files(), [])


class CliTest(unittest.TestCase):
    def test_real_tracked_files_have_no_overlap_and_dry_run_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(os.path.realpath(tmp), "settings.json")
            env = {**os.environ, "HOME": os.path.realpath(tmp)}
            result = subprocess.run(
                [sys.executable, SCRIPT, "--dry-run", "--settings", live],
                capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Dry run", result.stdout)
            self.assertFalse(os.path.exists(live))

    def test_symlink_refusal_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            live = os.path.join(tmp, "settings.json")
            os.symlink(os.path.join(tmp, "elsewhere.json"), live)
            result = subprocess.run(
                [sys.executable, SCRIPT, "--settings", live],
                capture_output=True, text=True, env={**os.environ, "HOME": tmp},
                stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 1)
            self.assertIn("symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
