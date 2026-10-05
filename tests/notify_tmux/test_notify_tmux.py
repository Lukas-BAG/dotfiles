"""Tests for the idle_prompt filter in claude_notify_tmux.sh.

A fake `tmux` on PATH reports a temp file as the pane tty, so a bell shows
up as a "\\a" written to that file.

Run from the repo root:  python3 -m unittest discover tests/notify_tmux
"""

import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO, "modules", "ai", ".local", "bin", "claude_notify_tmux.sh")


class NotifyTmuxTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.tty = os.path.join(self.dir, "pane_tty")
        open(self.tty, "w").close()
        fake = os.path.join(self.dir, "tmux")
        with open(fake, "w") as f:
            f.write(f"#!/bin/sh\necho {self.tty}\n")
        os.chmod(fake, 0o755)

    def run_script(self, stdin, in_tmux=True):
        env = {"PATH": f"{self.dir}:/usr/bin:/bin"}
        if in_tmux:
            env["TMUX_PANE"] = "%1"
        r = subprocess.run(["bash", SCRIPT], input=stdin, env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.tty) as f:
            return f.read()

    def payload(self, event, ntype=None, **extra):
        data = {"session_id": "s", "hook_event_name": event, **extra}
        if ntype:
            data["notification_type"] = ntype
        return json.dumps(data)

    def test_idle_prompt_does_not_ring(self):
        for text in (self.payload("Notification", "idle_prompt"),
                     json.dumps({"notification_type": "idle_prompt"}, indent=2),
                     '{"notification_type":"idle_prompt"}'):
            with self.subTest(text=text):
                self.assertEqual(self.run_script(text), "")

    def test_stop_rings(self):
        self.assertEqual(self.run_script(self.payload("Stop")), "\a")

    def test_permission_prompt_rings(self):
        self.assertEqual(
            self.run_script(self.payload("Notification", "permission_prompt")), "\a")

    def test_message_mentioning_idle_prompt_still_rings(self):
        text = self.payload("Notification", "permission_prompt",
                            message="idle_prompt is not the type here")
        self.assertEqual(self.run_script(text), "\a")

    def test_empty_or_invalid_stdin_rings_and_exits_zero(self):
        for text in ("", "not json", "{"):
            with self.subTest(text=text):
                self.assertEqual(self.run_script(text), "\a")
                open(self.tty, "w").close()

    def test_outside_tmux_does_nothing(self):
        for text in ("", self.payload("Stop"), self.payload("Notification", "idle_prompt")):
            with self.subTest(text=text):
                self.assertEqual(self.run_script(text, in_tmux=False), "")


if __name__ == "__main__":
    unittest.main()
