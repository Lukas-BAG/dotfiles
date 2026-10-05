# dotfilesV3

Personal dotfiles managed with [GNU Stow](https://www.gnu.org/software/stow/). Each tool or concern lives in its own module — only the modules you activate get symlinked into your home directory.

## What is this?

A modular dotfiles setup for a Linux desktop running i3. The core idea is that every piece of configuration belongs to a specific module. Activating a module symlinks its files into `$HOME` using stow; deactivating removes them. This makes it easy to maintain different configurations across machines (e.g. with or without laptop-specific tweaks, with or without AI tooling).

---

## Setup

### Remote bootstrap (fresh machine, nothing cloned yet)

```bash
curl -fsSL https://raw.githubusercontent.com/Beinmann/dotfilesv3/main/bootstrap.sh | bash
```

Installs `git` if missing, clones this repo into `~/Main/dotfilesv3`, and hands
off to the guided setup below. Refuses to run if your current directory is
already inside a git repo (to avoid cloning into the wrong place), and
refuses to run if `~/Main/dotfilesv3` already exists.

### Prerequisites

Install required packages (i3, stow, neovim, tilix, rofi, i3blocks, picom, and others):

```bash
bash Scripts/Initialization_and_Saving_State_Scripts/apt_install_programs.sh
```

### Initialize submodules

```bash
git submodule update --init --recursive
```

This pulls in the nvim config and the RAM monitor script.

### Guided setup (recommended)

```bash
python3 interactive_setup.py
```

This walks you through selecting modules (defaulting to your existing
`.module_list` selection, or `.module_list_template`'s suggestions if
`.module_list` doesn't exist yet), writes `.module_list` for you, runs a
`stow --simulate` dry run
to detect any pre-existing files that would conflict (e.g. `~/.bashrc` on a
fresh Debian/Ubuntu install), and lets you either move the exact conflicting
files to `dotfilesv3/backups/<timestamp>/` (non-destructive, default) or
delete them (requires typing `DELETE` to confirm). It only ever touches the
exact paths `stow` itself reports as conflicting — never a directory, never a
glob. Once conflicts are resolved it runs the real stow setup for you.

### Manual setup

If you'd rather do it by hand:

**Resolve conflicts first.** Stow creates symlinks from this repo into your
home directory. If files like `~/.bash_profile` already exist, stow
will refuse to overwrite them. You need to **remove or back up any
conflicting files before stowing**. Common conflicts to check: `~/.bash_profile`. (`~/.bashrc` and
`~/.profile` are not stowed; init appends a marked block to each.)

**Configure which modules to activate.** Copy the template module list and
edit it for your machine:

```bash
cp .module_list_template .module_list
```

The `.module_list` file is gitignored — it's per-machine. Uncomment or add
the modules you want. See the Modules section below.

**Stow:**

```bash
python3 init_or_deinit_stow.py
```

Other options:
```bash
python3 init_or_deinit_stow.py -D   # unstow everything (remove all symlinks)
python3 init_or_deinit_stow.py -R   # restow (unstow then stow again, useful after moving files)
```

**Claude Code settings (`ai` module):** `~/.claude/settings.json` is not
stowed, because Claude Code writes to it during normal use. The settings
shared by every machine are tracked in `claude_settings/` and merged into the
real file by `sync_claude_settings.py`, which the stow helper runs after
stowing whenever `ai` is selected. Run it by hand after changing the tracked
settings:

```bash
python3 sync_claude_settings.py --dry-run   # show what would change
python3 sync_claude_settings.py             # apply; asks before overriding local values
```

See `claude_settings/README.md` for how defaults and enforced settings differ.

---

## Modules

| Module | Description |
|---|---|
| `base` | Core shell setup: `bashrc.sh` (hooked into `~/.bashrc` by a managed block), shell settings, aliases, functions, plugins, plus git/tmux/vim-style tool configs and small helper scripts. No desktop config — that is in `i3`. The foundation — should always be active. |
| `i3` | The whole desktop/session setup: i3 configuration with the i3blocks status bar, workspace scripts and in-repo status bar blocks (volume, CPU, memory, battery), plus sway, dunst, X resources and keybindings (`.Xresources`, `.xbindkeysrc`), the custom XKB layout, startup/lock/wallpaper scripts, `myScreenshot` and the wallpaper. Leave it out on headless machines. |
| `nvim` | Neovim configuration (submodule pointing to a separate nvim config repo). |
| `vim` | Vim configuration for when neovim isn't available. |
| `scripts` | Miscellaneous helper scripts (RAM monitor etc.) managed as submodules. |
| `services` | Systemd user services. |
| `ai` | AI tooling — Claude usage monitor script, notification hook script and shell alias (Claude settings: see `claude_settings/`). Opt-in: only activate on machines where you use Claude. |
| `laptop_adaptations` | Laptop-specific tweaks (touchpad natural scrolling, tapping). Opt-in: activate on laptops alongside `base` (and `i3` on a laptop desktop). |

### Module conventions

Shell aliases, functions, and plugin/tool integrations that are module-specific live under:

```
~/.config/dotfiles/
  aliases.d/      # one .sh file per module
  functions.d/    # one .sh file per module
  plugins.d/      # one .sh file per module
  settings.d/     # one .sh file per module
  system_local/   # machine-specific overrides (not tracked in git)
```

These directories are glob-sourced by `~/.config/dotfiles/bashrc.sh` (which a managed block in your own `~/.bashrc` sources) at shell startup. If a module isn't stowed, its file doesn't exist and nothing is loaded — no conditionals needed.

### Machine-specific config

For tooling that is local to a single machine and should not be tracked in git (e.g. nvm, conda, company-specific paths), the init script automatically creates these files on first run if they don't exist:

```
~/.config/dotfiles/system_local/bashrc.sh      # sourced by bashrc.sh at startup
~/.config/dotfiles/system_local/i3_config_addon # included by i3 config at startup
```

These files are never part of this repo and are never deleted by `-D`/`-R` — add machine-specific config to them freely.

---

## Repository structure

```
dotfilesV3/
├── modules/               # one directory per module, stowed to $HOME
├── sys_modules/           # modules stowed to / (requires sudo stow)
├── Scripts/               # repo-level utility scripts (not stowed)
├── interactive_setup.py   # guided setup: select modules, resolve conflicts, stow
├── init_or_deinit_stow.py # stow helper
├── claude_settings/       # shared Claude Code settings (not stowed)
├── sync_claude_settings.py # merges claude_settings/ into ~/.claude/settings.json
├── tests/                 # unittest suites (python3 -m unittest discover tests/<name>)
├── backups/               # files moved aside by interactive_setup.py, settings backups (gitignored)
├── .module_list_template  # template for per-machine module selection
└── .module_list           # your active modules (gitignored)
```

`sys_modules` works the same as `modules` but targets `/` as the stow target instead of `$HOME`. Use it for system-wide config files (e.g. under `/etc`). Activate these by prefixing the module name with `sys/` in `.module_list`, e.g. `sys/base`.
