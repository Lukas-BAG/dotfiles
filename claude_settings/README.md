# Shared Claude Code settings

Not stowed. `sync_claude_settings.py` (repo root) merges these into the
real, per-machine `~/.claude/settings.json`. Claude Code writes to that file
during normal use (permission grants, `/model`, key reordering), so it is
never symlinked into this repo.

- `defaults.json`: set only where the live file lacks the key. For values a
  machine may choose for itself, e.g. `model`.
- `enforced.json`: should hold on every machine. Missing keys are added.
  If a key already has a different value, the script shows a diff and asks
  before overriding it. A declined override is asked about again on the
  next run. With no terminal, overrides are skipped and listed.

Rule of thumb: a setting is enforced unless there is a reason one machine
should differ.

Merge rules:

- Objects are merged key by key. Keys not mentioned here are left alone at
  any depth, so a machine-local `hooks.PreToolUse` survives an enforced
  `hooks.Stop`.
- Everything else is one value, lists included. A list is never combined
  with the live list; any difference is an override to confirm.
- A key path may appear in only one of the two files.
- Keys removed from these files are **not** removed from the live file;
  delete them by hand. A later addition could be a third file listing key
  paths to delete.

The script refuses to work when `~/.claude/settings.json` (or `~/.claude`)
is a symlink. On a machine that used to stow `settings.json`, replace the
link with a real copy of its content first. Before each write, the live
file is backed up to `backups/<timestamp>/claude-settings.json`.
