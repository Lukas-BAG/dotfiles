#!/bin/bash
# No strict mode: long-running i3 event subscriber; a failed i3-msg or jq
# for one event must not end the loop.

# Created 18.12.2024 with the help of ChatGPT
# but I had the exact same script somewhere else and I just cant remember where
#
# This script when running will subscribe to window change notifications from i3 and will flash the border for a sec whenever the window changes

border_size=2

i3-msg -t subscribe -m '[ "window" ]' | while read -r event; do
    window_id=$(echo $event | jq '.container.id')
    i3-msg "[con_id=$window_id]" border pixel $border_size
    sleep 0.1
    i3-msg "[con_id=$window_id]" border pixel 0
done

