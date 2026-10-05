#!/bin/bash
set -euo pipefail
# Script to switch workspace order around
# so workspace 20 would become 22 and workspace 22 would become workspace 20 in turn

current_workspace=$(i3-msg -t get_workspaces | jq '.[] | select(.focused==true).name | .[0:1]' | tr -d '"')



main_workspace="${current_workspace}0"
secondary_workspace="${current_workspace}${current_workspace}"

# notify-send "main workspace" "$main_workspace"
# notify-send "secondary workspace" "$secondary_workspace"

i3-msg rename workspace "$main_workspace" to "temp"
i3-msg rename workspace "$secondary_workspace" to "$main_workspace"
i3-msg rename workspace "temp" to "$secondary_workspace"

# if [ "$current_workspace_full" = "$main_workspace" ]; then
#   i3-msg workspace "$main_workspace"
# else
#   i3-msg workspace "$secondary_workspace"
# fi
