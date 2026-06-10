#!/bin/sh
# Opens a new tmux window at the lowest free index (fills gaps before appending).
# $1 = starting directory, passed from tmux run-shell via #{pane_current_path}
next=$(tmux list-windows -F "#I" | sort -n | awk 'BEGIN{i=1;f=0}{if($1!=i){print i;f=1;exit}i++}END{if(!f)print i}')
tmux new-window -t "$next" -c "${1:-$HOME}"
