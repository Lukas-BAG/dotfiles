#!/bin/sh

# Desktop helpers; they live here because the scripts they call ship with the
# i3 module (base alone has no desktop).
alias restartxbindkeys="killall xbindkeys; xbindkeys"
alias lock="$HOME/Main/Scripts/Helper_Scripts/lock_screen.sh"
alias mySetBackground="$HOME/Main/Scripts/Helper_Scripts/set_background.sh"
