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

This pulls in the nvim config, RAM monitor script, and i3blocks-contrib scripts.

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
home directory. If files like `~/.bashrc` or `~/.profile` already exist, stow
will refuse to overwrite them. You need to **remove or back up any
conflicting files before stowing**. Common conflicts to check: `~/.bashrc`,
`~/.bash_profile`, `~/.profile`.

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

---

## Modules

| Module | Description |
|---|---|
| `base` | Core shell setup: `.bashrc`, shell settings, aliases, functions, plugins. The foundation — should always be active. |
| `i3` | Full i3 window manager configuration including i3blocks status bar, workspace scripts, and the i3blocks-contrib scripts as a submodule. |
| `nvim` | Neovim configuration (submodule pointing to a separate nvim config repo). |
| `vim` | Vim configuration for when neovim isn't available. |
| `scripts` | Miscellaneous helper scripts (RAM monitor etc.) managed as submodules. |
| `services` | Systemd user services. |
| `ai` | AI tooling — Claude usage monitor script and shell alias. Opt-in: only activate on machines where you use Claude. |
| `laptop_adaptations` | Laptop-specific tweaks (touchpad natural scrolling, tapping). Opt-in: activate on laptops instead of or alongside `base`. |

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

These directories are glob-sourced by `.bashrc` at shell startup. If a module isn't stowed, its file doesn't exist and nothing is loaded — no conditionals needed.

### Machine-specific config

For tooling that is local to a single machine and should not be tracked in git (e.g. nvm, conda, company-specific paths), the init script automatically creates these files on first run if they don't exist:

```
~/.config/dotfiles/system_local/bashrc.sh      # sourced by .bashrc at startup
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
├── backups/               # conflicting files moved aside by interactive_setup.py (gitignored)
├── .module_list_template  # template for per-machine module selection
└── .module_list           # your active modules (gitignored)
```

`sys_modules` works the same as `modules` but targets `/` as the stow target instead of `$HOME`. Use it for system-wide config files (e.g. under `/etc`). Activate these by prefixing the module name with `sys/` in `.module_list`, e.g. `sys/base`.
