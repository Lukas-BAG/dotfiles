# Entry point for login shells, sourced from the managed block that
# init_or_deinit_stow.py appends to ~/.profile. Keep it POSIX sh: ~/.profile
# is also read by dash and display managers.
# Machine-specific entries belong in ~/.profile_system_specific_extensions or
# the system-specific config file below.

export EDITOR=vim
export VISUAL=vim

# PATH additions; skip entries already present so re-sourcing is harmless.
_dotfiles_path_prepend() {
    case ":$PATH:" in
        *":$1:"*) ;;
        *) PATH="$1:$PATH" ;;
    esac
}
_dotfiles_path_append() {
    case ":$PATH:" in
        *":$1:"*) ;;
        *) PATH="$PATH:$1" ;;
    esac
}

# user's private bin dirs, if they exist
[ -d "$HOME/bin" ] && _dotfiles_path_prepend "$HOME/bin"
[ -d "$HOME/.local/bin" ] && _dotfiles_path_prepend "$HOME/.local/bin"
_dotfiles_path_append /sbin
_dotfiles_path_append /usr/sbin
export PATH
unset -f _dotfiles_path_prepend _dotfiles_path_append

if [ -f "$HOME/Main/Additional_Config/system_specific_profile_config.sh" ]; then
    . "$HOME/Main/Additional_Config/system_specific_profile_config.sh"
fi

profile_extensions=$HOME/.profile_system_specific_extensions
if [ -f "$profile_extensions" ]; then
    . "$profile_extensions"
fi
unset profile_extensions

# Login bash shells read ~/.profile instead of ~/.bashrc when there is no
# ~/.bash_profile, and the managed block can leave ~/.profile with nothing else
# in it (the distro default that does this is gone). Pull ~/.bashrc in, as the
# Debian default ~/.profile does -- unless it already ran earlier in this
# shell (bashrc.sh sets the variable), so it isn't sourced twice.
if [ -n "$BASH_VERSION" ] && [ -z "$DOTFILES_BASHRC_LOADED" ] && [ -f "$HOME/.bashrc" ]; then
    . "$HOME/.bashrc"
fi
