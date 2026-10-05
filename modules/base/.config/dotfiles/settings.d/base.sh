# Shell behaviour for interactive bash. Self-contained: it sets everything we
# want on any system, without relying on the distro's ~/.bashrc, and is safe
# to source repeatedly (reloadbash). Plain assignments repeat harmlessly; only
# the completion setup is guarded.

# Vi keybindings in the shell
set -o vi


########## History ##########
# Sizes are our choice (3x Debian's defaults), not inherited.
HISTSIZE=3000
HISTFILESIZE=6000
shopt -s histappend

# don't put duplicate lines or lines starting with space in the history.
HISTCONTROL=ignoreboth

# Update LINES and COLUMNS after each command
shopt -s checkwinsize


########## Prompt ##########
# user@host:dir$, coloured when the terminal supports it (checked via tput,
# which also covers terminals whose TERM doesn't end in -256color).
if [ "${TERM:-dumb}" != dumb ] && command -v tput >/dev/null 2>&1 \
        && tput setaf 1 >/dev/null 2>&1; then
    PS1='\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
else
    PS1='\u@\h:\w\$ '
fi

# LS_COLORS for ls; honours a personal ~/.dircolors. The colour aliases
# themselves are in aliases.d/base.sh.
if command -v dircolors >/dev/null 2>&1; then
    if [ -r ~/.dircolors ]; then
        eval "$(dircolors -b ~/.dircolors)"
    else
        eval "$(dircolors -b)"
    fi
fi


########## Completion ##########
# Loaded once: bash-completion sets BASH_COMPLETION_VERSINFO, and sourcing it
# a second time (reloadbash, or the distro .bashrc having loaded it already)
# would only redo the work.
if [ -z "${BASH_COMPLETION_VERSINFO:-}" ] && ! shopt -oq posix; then
    if [ -f /usr/share/bash-completion/bash_completion ]; then
        . /usr/share/bash-completion/bash_completion
    elif [ -f /etc/bash_completion ]; then
        . /etc/bash_completion
    fi
fi
