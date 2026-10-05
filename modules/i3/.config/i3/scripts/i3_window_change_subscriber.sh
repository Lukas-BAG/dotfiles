#!/bin/bash
# No strict mode: long-running i3 event subscriber; a failed i3-msg for one
# event must not end the loop.

# make a border appear briefly whenever the focussed window changes
# written by ChatGPT (at least the listener. The idea and the part that is executed came from me)
# uses jq to parse the i3 events and listens for window change event
i3-msg -t subscribe -m '[ "window" ]' | jq --unbuffered -r 'select(.change == "focus") | .container.id' | while read -r id; do
    # Apply a border to the focused window
    i3-msg "[con_id=$id] border pixel 2"
    sleep 0.2
    i3-msg "[con_id=$id] border pixel 0"
done
