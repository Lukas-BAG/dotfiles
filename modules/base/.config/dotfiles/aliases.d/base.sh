#!/bin/sh

alias ..='cd ..'
alias cds='cds() { cd "$1" && ls; }; cds'
alias restartxbindkeys="killall xbindkeys; xbindkeys"
alias reloadbashrc="source ~/.bashrc"
alias reloadbash="source ~/.bashrc"
alias :r=reloadbash
alias :q="exit"
alias editbashrc="nvim ~/.bashrc"
alias r="ranger_cd"
alias lock="$HOME/Main/Scripts/Helper_Scripts/lock_screen.sh"
alias notify="timed_notification.sh"
alias myTimer="timed_notification.sh"
alias myNotify="timed_notification.sh"
alias myTimerLog="echo 'Last 20 lines of notification log' && echo '' && cat ~/.notification_log | tail -n 20"
alias myOpen="open . & disown"
alias mySetBackground="$HOME/Main/Scripts/Helper_Scripts/set_background.sh"
alias ram="python3 $HOME/Main/Scripts/RAM_Script_Python/main.py"
alias myNautilusAndExit="nautilus . & exit"
alias brown_noise="ffplay -nodisp $HOME/Main/Brown_Noise.mp3"
alias mainVenvActivate=". ~/Main/Programming/Python/main_venv_3_10_14/bin/activate"
alias darkMode="gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'"
alias lightMode="gsettings set org.gnome.desktop.interface color-scheme 'default'"

alias lsb="lsblk -f"
alias show_file_sizes="du -h --max-depth=1 | sort -h -r"
alias myrsync="rsync -av --info=progress2"

alias sshAddKey='[ -z "$SSH_AUTH_SOCK" ] && eval "$(ssh-agent -s)"; ssh-add ~/.ssh/id_ed'

