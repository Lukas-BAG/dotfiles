#!/bin/bash

autokey-gtk &

source .xinitrc

firefox &

$HOME/Main/Tools/firefox/firefox &

tilix -e tmux &

xbindkeys

sleep 2
WINDOW_ID=$(xdotool search --sync --onlyvisible --class "Firefox" | head -1)
xdotool windowmove $WINDOW_ID 1894 -22
xdotool windowsize $WINDOW_ID 1920 100%
