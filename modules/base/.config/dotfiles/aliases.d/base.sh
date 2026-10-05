#!/bin/sh

alias ..='cd ..'
alias cds='cds() { cd "$1" && ls; }; cds'
alias reloadbash="source ~/.bashrc"
alias :r=reloadbash
alias :q="exit"
alias editbashrc="nvim ~/.bashrc"
alias r="ranger_cd"
alias myTimer="timed_notification.sh"
alias myTimerLog="echo 'Last 20 lines of notification log' && echo '' && cat ~/.notification_log | tail -n 20"
alias myOpen="open . & disown"
# Only where the scripts module's RAM script is stowed.
if [ -f "$HOME/Main/Scripts/RAM_Script_Python/main.py" ]; then
    alias ram="python3 $HOME/Main/Scripts/RAM_Script_Python/main.py"
fi
alias myNautilusAndExit='tmux split-window "open . & exit"'
alias darkMode="gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'"
alias lightMode="gsettings set org.gnome.desktop.interface color-scheme 'default'"

alias lsb="lsblk -f"
alias show_file_sizes="du -h --max-depth=1 | sort -h -r"
alias myrsync="rsync -av --info=progress2"
alias claude-box="ssh -p 2222 root@127.0.0.1"
alias trufflehog="trufflehog --no-verification"
alias lsg="ls | grep -i"

# Colour output for ls/grep, only where the tools support --color (GNU).
if ls --color=auto / >/dev/null 2>&1; then
    alias ls='ls --color=auto'
fi
if echo x | grep --color=auto -q x >/dev/null 2>&1; then
    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

alias lse='everything entries'  # list the entries of the Everything dir you're in, see ~/.local/bin/everything
