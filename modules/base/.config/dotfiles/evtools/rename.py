"""Change the description/tags of an entry's sidecar (what `everything rename` does).

Run inside an entry dir: the sidecar "<entry>_<description>[_@tag...].md" next
to it is renamed to whatever is typed in $EDITOR for the part after the entry
id. The name is validated first (see `validate`), so the result is one that
`everything check` has no naming complaint about; on a problem the editor is
opened again with the error shown in a comment, like `git commit`.

Writes nothing but one os.rename, and never over an existing file.
"""

import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass

from . import entries

MAX_LEN = 80  # of the editable part, i.e. without the entry id and the extension
ALLOWED_RE = re.compile(r"[A-Za-z0-9_@]*")
COMMENT_HELP = [
    "# Edit the sidecar name: description and tags, without the entry id and",
    "# extension. Words are joined by _, each tag starts with _@, e.g.",
    "#   working_on_dotfiles_@ai",
    f"# Letters, digits and _ only, at most {MAX_LEN} characters. Lines starting",
    "# with # are ignored. An empty line or no change cancels the rename.",
]


class Refusal(Exception):
    """Why nothing was renamed; the message is shown as is."""


class Invalid(Exception):
    """The typed name is unusable; the message says why."""


@dataclass(frozen=True)
class Target:
    entry_dir: str
    sidecar_path: str
    sidecar: entries.SidecarName

    @property
    def editable(self) -> str:
        """The sidecar name without "<entry>_" and the extension."""
        name = self.sidecar.name
        return name[len(self.sidecar.entry.name) + 1:len(name) - len(self.sidecar.ext)]


def find_target(cwd: str) -> Target:
    """The nearest dir at or above `cwd` that is an entry with exactly one sidecar."""
    path = cwd
    while True:
        parent, name = os.path.split(path)
        if not name:
            break
        if entries.parse_entry(name):
            found = [s for s in entries.list_sidecars(parent) if s.entry.name == name]
            if len(found) == 1:
                return Target(path, os.path.join(parent, found[0].name), found[0])
        path = parent
    raise Refusal("not inside an entry dir whose parent holds exactly one sidecar "
                  "for it - nothing renamed")


def validate(text: str, target: Target) -> str:
    """The new sidecar file name for the typed `text`; Invalid if it can't be used."""
    if re.search(r"\s", text):
        raise Invalid("the name contains whitespace; join words with _")
    if "/" in text:
        raise Invalid("the name contains '/'")
    bad = sorted(set(re.sub(r"[A-Za-z0-9_@]", "", text)))
    if bad:
        raise Invalid("characters not allowed: " + " ".join(repr(c) for c in bad)
                      + " (letters, digits, _ and @tags only)")
    if len(text) > MAX_LEN:
        raise Invalid(f"the name is {len(text)} characters, at most {MAX_LEN} allowed")
    segments = text.split("_")
    if "" in segments:
        raise Invalid("the name has a leading, trailing or doubled _")
    seen_tag = False
    for i, seg in enumerate(segments):
        if "@" in seg[1:] or seg == "@" or seg.startswith("@@"):
            raise Invalid(f"malformed tag '{seg}': a tag is _@ followed by letters or digits")
        if seg.startswith("@"):
            if i == 0:
                raise Invalid("the name must start with a description word, not a tag")
            seen_tag = True
        elif seen_tag:
            raise Invalid(f"'{seg}' comes after a tag and would become part of it; "
                          "put all tags last")
    name = f"{target.sidecar.entry.name}_{text}{target.sidecar.ext}"
    parsed = entries.parse_sidecar(name)
    if parsed is None or parsed.entry.name != target.sidecar.entry.name or not parsed.description:
        raise Invalid("the result would not be a valid sidecar name")
    if text != target.editable and os.path.lexists(os.path.join(os.path.dirname(target.sidecar_path), name)):
        raise Invalid(f"'{name}' already exists")
    return name


def _run_editor(path: str) -> bool:
    editor = shlex.split(os.environ.get("EDITOR") or "vim")
    try:
        return subprocess.run([*editor, path]).returncode == 0
    except OSError as e:
        raise Refusal(f"can't run the editor '{editor[0]}': {e.strerror}")


def _read_line(path: str) -> str:
    """The first non-blank line that isn't a # comment, without its newline."""
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.strip() and not line.startswith("#"):
                return line
    return ""


def ask_name(target: Target) -> str | None:
    """Edit the name in $EDITOR until it is valid; the new file name, or None to cancel.

    Cancelled by an editor error (:cq), an empty line, no change at all, or
    saving an invalid name again without touching it.
    """
    fd, path = tempfile.mkstemp(prefix="everything-rename-", suffix=".txt")
    try:
        text, error = target.editable, None
        while True:
            with os.fdopen(fd, "w") as f:
                f.write(text + "\n")
                if error:
                    f.write(f"# ERROR: {error}\n")
                f.write("\n".join(COMMENT_HELP) + "\n")
            if not _run_editor(path):
                return None
            edited = _read_line(path)
            if not edited or (edited == text and error) or (edited == target.editable):
                return None
            text = edited
            try:
                return validate(edited, target)
            except Invalid as e:
                error = str(e)
            fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    finally:
        os.unlink(path)


def rename(target: Target, new_name: str) -> str:
    """Rename the sidecar, refusing to replace anything; returns the new path."""
    new_path = os.path.join(os.path.dirname(target.sidecar_path), new_name)
    if os.path.lexists(new_path):
        raise Refusal(f"'{new_name}' already exists, refusing to touch it")
    os.rename(target.sidecar_path, new_path)
    return new_path
