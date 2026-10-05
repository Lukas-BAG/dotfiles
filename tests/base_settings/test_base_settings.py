"""Tests that settings.d/base.sh + aliases.d/base.sh are self-contained.

Run from the repo root:  python3 -m unittest discover tests/base_settings
"""

import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOTFILES = os.path.join(REPO, "modules", "base", ".config", "dotfiles")
SETTINGS = os.path.join(DOTFILES, "settings.d", "base.sh")
ALIASES = os.path.join(DOTFILES, "aliases.d", "base.sh")
SKEL = "/etc/skel/.bashrc"


class BaseSettingsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.realpath(tmp.name)
        os.makedirs(os.path.join(self.home, ".config"))
        os.symlink(DOTFILES, os.path.join(self.home, ".config", "dotfiles"))

    def bash(self, script, rcfile=None):
        """Run `script` in a clean-env interactive bash; return stdout."""
        env = {"HOME": self.home, "TERM": "xterm-256color",
               "PATH": "/usr/bin:/bin"}
        cmd = ["bash", "--rcfile", rcfile or os.devnull, "-i", "-c", script]
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def write_rc(self, with_skel):
        rc = os.path.join(self.home, ".bashrc")
        if with_skel:
            shutil.copy(SKEL, rc)
        with open(rc, "a") as f:
            f.write("\n[ -f ~/.config/dotfiles/bashrc.sh ] && "
                    ". ~/.config/dotfiles/bashrc.sh\n")
        return rc

    def test_sets_behaviour_without_distro_rc(self):
        out = self.bash(f". {SETTINGS}; . {ALIASES}; "
                        'echo "$HISTSIZE $HISTCONTROL"; set -o | grep "^vi "; '
                        "shopt histappend checkwinsize; echo \"$PS1\"; alias ls grep")
        self.assertIn("3000 ignoreboth", out)
        self.assertRegex(out, r"vi\s+on")
        self.assertIn("histappend", out)
        self.assertIn("checkwinsize", out)
        self.assertIn("\\[\\033[01;32m\\]", out)  # coloured prompt
        self.assertIn("alias ls='ls --color=auto'", out)
        self.assertIn("alias grep='grep --color=auto'", out)

    def test_dumb_terminal_gets_plain_prompt(self):
        env = {"HOME": self.home, "TERM": "dumb", "PATH": "/usr/bin:/bin"}
        r = subprocess.run(["bash", "--norc", "-i", "-c",
                            f'. {SETTINGS}; echo "$PS1"'],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), r"\u@\h:\w\$")

    def test_duplicate_and_machine_specific_aliases_are_gone(self):
        out = self.bash(f". {ALIASES}; alias")
        names = {line.split("=")[0][len("alias "):] for line in out.splitlines()}
        for kept in ("reloadbash", ":r", "myTimer", "myTimerLog", "myNautilusAndExit"):
            self.assertIn(kept, names)
        for gone in ("reloadbashrc", "notify", "myNotify", "brown_noise",
                     "mainVenvActivate"):
            self.assertNotIn(gone, names)

    def test_ram_alias_only_when_script_exists(self):
        self.assertNotIn("alias ram=", self.bash(f". {ALIASES}; alias"))
        script = os.path.join(self.home, "Main/Scripts/RAM_Script_Python")
        os.makedirs(script)
        open(os.path.join(script, "main.py"), "w").close()
        self.assertIn("alias ram=", self.bash(f". {ALIASES}; alias"))

    def test_no_debian_leftovers(self):
        with open(SETTINGS) as f:
            text = f.read()
        self.assertNotIn("debian_chroot", text)
        self.assertNotIn("force_color_prompt", text)

    def test_sourcing_twice_is_idempotent(self):
        once = self.bash(f". {SETTINGS}; . {ALIASES}; "
                         'echo "$PS1|$HISTSIZE|$LS_COLORS"; alias; shopt -p')
        twice = self.bash(f". {SETTINGS}; . {ALIASES}; . {SETTINGS}; . {ALIASES}; "
                          'echo "$PS1|$HISTSIZE|$LS_COLORS"; alias; shopt -p')
        self.assertEqual(once, twice)

    @unittest.skipUnless(os.path.isfile("/usr/share/bash-completion/bash_completion"),
                         "bash-completion not installed")
    def test_completion_loaded_once(self):
        # A hook that counts how often bash_completion is sourced.
        out = self.bash(
            f'. {SETTINGS}; v1="$BASH_COMPLETION_VERSINFO"; '
            f'n1=$(complete | wc -l); . {SETTINGS}; . {SETTINGS}; '
            'n2=$(complete | wc -l); echo "$v1|$BASH_COMPLETION_VERSINFO|$n1|$n2"')
        v1, v2, n1, n2 = out.strip().split("|")
        self.assertTrue(v1)
        self.assertEqual((v1, n1), (v2, n2))

    @unittest.skipUnless(os.path.isfile(SKEL), "no /etc/skel/.bashrc")
    def test_full_startup_with_and_without_skel(self):
        for with_skel in (False, True):
            with self.subTest(with_skel=with_skel):
                rc = self.write_rc(with_skel)
                out = self.bash('set -o | grep "^vi "; echo "$HISTSIZE"; '
                                'alias ls; echo "$PS1"', rcfile=rc)
                self.assertRegex(out, r"vi\s+on")
                self.assertIn("3000", out)
                self.assertIn("alias ls='ls --color=auto'", out)
                self.assertIn("\\[\\033[01;32m\\]", out)

    @unittest.skipUnless(os.path.isfile(SKEL), "no /etc/skel/.bashrc")
    def test_reload_on_top_of_skel_is_idempotent(self):
        rc = self.write_rc(True)
        script = 'echo "$PS1|$HISTSIZE"; alias; complete | wc -l'
        once = self.bash(script, rcfile=rc)
        twice = self.bash(f". {rc}; " + script, rcfile=rc)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
