#!/bin/bash

tilix -e tmux &

# start firefox on workspace 2 in the background
i3-msg 'workspace 20; exec $HOME/Main/Tools/firefox/firefox; workspace back_and_forth'
