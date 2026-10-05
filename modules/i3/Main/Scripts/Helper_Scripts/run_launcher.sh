#!/bin/sh
# Script to run rofi if possible; if not run dmenu

if command -v rofi >/dev/null 2>&1; then
    rofi -show drun
else
    notify-send "Launcher" "Rofi not found — using dmenu instead"
    dmenu_run
fi
