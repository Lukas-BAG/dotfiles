# Entry point for interactive bash, sourced from the managed block that
# init_or_deinit_stow.py appends to ~/.bashrc.

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac

# Tells profile.sh that ~/.bashrc already ran in this shell. Not exported.
DOTFILES_BASHRC_LOADED=1

for f in ~/.config/dotfiles/settings.d/*.sh; do [ -f "$f" ] && . "$f"; done
for f in ~/.config/dotfiles/aliases.d/*.sh; do [ -f "$f" ] && . "$f"; done
for f in ~/.config/dotfiles/functions.d/*.sh; do [ -f "$f" ] && . "$f"; done
for f in ~/.config/dotfiles/plugins.d/*.sh; do [ -f "$f" ] && . "$f"; done

if [ -f ~/.config/dotfiles/system_local/bashrc.sh ]; then
    . ~/.config/dotfiles/system_local/bashrc.sh
fi


####################### PATH
export PATH=/usr/local/node/bin:$PATH
export PATH="$PATH:~/bin"
export PATH="$PATH:$HOME/Main/Tools/decker"
export PATH=$PATH:/sbin:/usr/sbin


# opencode
export PATH=/home/user1/.opencode/bin:$PATH
