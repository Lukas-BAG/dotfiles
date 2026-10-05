#!/bin/sh

tmp=$(mktemp /tmp/lockimg.XXXXXX.png) || exit 1

if convert "$HOME/Main/Data/pexels-dids-3306986.jpg" -resize 1920x1080^ -gravity center -extent 1920x1080 "$tmp"; then
    i3lock --nofork -i "$tmp"
else
    i3lock
fi

rm -f "$tmp"
