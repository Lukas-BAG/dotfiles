#!/bin/bash
# Apply the custom XKB layout from ~/.config/xkb/symbols/custom and
# set the key repeat rate. Revert layout with: setxkbmap -layout de -model pc105
set -euo pipefail
: "${DISPLAY:?DISPLAY not set; run inside an X session}"

xset r rate 333 67

setxkbmap -layout custom -variant basiccustom -print \
    | xkbcomp -I"$HOME/.config/xkb" -xkm - "$DISPLAY"

echo "keyboard-remap: applied custom layout to $DISPLAY"
