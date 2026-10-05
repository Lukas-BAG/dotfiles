#!/usr/bin/env bash
set -euo pipefail
# I am testing using alt workspaces (so I have workspace 10 which is equivalent to workspace 1 but then also have workspace 11 which would be equivalent to workspace 1 alt)
# This script makes it so (in combination with updating i3config) that I can more easily move things to the alt windows
# 
# Example
#   Moving something to workspace 3 with this script would move it to workspace 3 as you'd expect
#   unless you are already on workspace 3 then it would move it to workspace 3 alt
set -- "${1:?usage: $0 <workspace number>}"
current_workspace=$(i3-msg -t get_workspaces | jq '.[] | select(.focused==true).name' | tr -d '"')

if [ "$current_workspace" = "${1}0" ]; then
  i3-msg move container to workspace "${1}${1}"
else
  i3-msg move container to workspace "${1}0"
fi
