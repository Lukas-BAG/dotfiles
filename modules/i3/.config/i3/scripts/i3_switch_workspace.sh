#!/bin/bash
set -euo pipefail

current_workspace=$(i3-msg -t get_workspaces | jq '.[] | select(.focused==true).name' | tr -d '"')
echo "$current_workspace" > /tmp/i3_prev_workspace

i3-msg workspace "$1"
