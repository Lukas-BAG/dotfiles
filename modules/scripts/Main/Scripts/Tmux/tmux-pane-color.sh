#!/bin/sh
# Assigns a random subtle background color to the active pane.
# Colors are dark hue-shifted variants — visually distinct but not distracting.
colors="#20202a #202a20 #24202a #2a2020 #2a2420 #202a2a #242a20 #2a2024"
count=8
index=$(awk -v seed="$(date +%N 2>/dev/null || date +%s)$$" -v n="$count" \
  'BEGIN{srand(seed+0); print int(rand()*n)}')
color=$(printf '%s\n' $colors | awk -v i="$index" 'NR==i+1')
pane_id="${1:-$(tmux display-message -p '#{pane_id}')}"
tmux select-pane -t "$pane_id" -P "bg=$color"
