"""Small interactive helpers for scan/mark/unmark: yes/no, and picking from a list.

Questions and menus go to stderr, answers come from stdin; fzf (when found)
draws on /dev/tty, so these also work inside $(...).
"""

import shutil
import subprocess
import sys


def confirm(prog: str, question: str, yes: bool = False, hint: str = "") -> bool:
    """Ask `question` [y/N]; True if answered yes. `yes` skips the question.

    Without a terminal on stdin nothing is asked and the answer is no, with a
    message (plus `hint`, e.g. "use --yes"): such a run can't have been looked at.
    """
    if yes:
        return True
    if not sys.stdin.isatty():
        print(f"{prog}: {question} - no terminal to ask on, refusing{hint}", file=sys.stderr)
        return False
    print(f"{question} [y/N] ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def _fzf(rows: list[str], multi: bool, prompt: str) -> list[str]:
    """Selected rows' hidden values (the part after the tab); [] if none was chosen."""
    cmd = ["fzf", "--delimiter=\t", "--with-nth=1", "--layout=reverse", f"--prompt={prompt} > "]
    if multi:
        cmd += ["--multi", "--header=TAB marks several, ENTER confirms"]
    res = subprocess.run(cmd, input="\n".join(rows) + "\n", stdout=subprocess.PIPE, text=True)
    if res.returncode != 0:
        return []
    return [line.split("\t")[-1] for line in res.stdout.splitlines() if line]


def choose(prog: str, items: list[tuple[str, str]], prompt: str,
           multi: bool = False) -> list[str]:
    """Let the user pick from (label, value) items; the chosen values, [] for none.

    fzf if available (TAB multi-selects), else a numbered list answered on
    stdin: numbers separated by spaces (several only with `multi`; "all" too).
    """
    if shutil.which("fzf"):
        return _fzf([f"{label}\t{value}" for label, value in items], multi, prompt)
    for i, (label, _) in enumerate(items, 1):
        print(f"{i:>3}) {label}", file=sys.stderr)
    ask = "numbers separated by spaces, 'all', or nothing for none" if multi else "number"
    print(f"{prog}: {prompt} ({ask}): ", end="", file=sys.stderr, flush=True)
    answer = sys.stdin.readline().strip().lower()
    if not answer:
        return []
    if multi and answer == "all":
        return [v for _, v in items]
    picked = []
    for word in answer.replace(",", " ").split():
        if not word.isdigit() or not 1 <= int(word) <= len(items):
            print(f"{prog}: '{word}' is not one of 1-{len(items)}, nothing chosen",
                  file=sys.stderr)
            return []
        picked.append(items[int(word) - 1][1])
    if len(picked) > 1 and not multi:
        print(f"{prog}: pick one, nothing chosen", file=sys.stderr)
        return []
    return list(dict.fromkeys(picked))
