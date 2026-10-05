#!/usr/bin/env bash
# No strict mode: persistent block; pactl and the grep filter may fail while the
# sound server restarts, and the loop has to keep going.
# i3blocks volume block (persistent): icon + percentage of the default sink,
# redrawn on every pulse/pipewire sink event. Display only, no click handling.
# Needs markup=pango and the FontAwesome font (see battery_status.sh).

fa() { printf "<span font='FontAwesome'>%s</span>" "$1"; }
# UTF-8 bytes so the output doesn't depend on the locale.
high=$(fa $'\xef\x80\xa8') med=$(fa $'\xef\x80\xa7') low=$(fa $'\xef\x80\xa6')
muted="$low$(fa $'\xe2\x83\xa0')"   # speaker + combining enclosing circle

print_volume() {
    local vol mute
    vol=$(pactl get-sink-volume @DEFAULT_SINK@ 2>/dev/null | grep -o '[0-9]*%' | head -1)
    mute=$(pactl get-sink-mute @DEFAULT_SINK@ 2>/dev/null)
    if [[ -z $vol ]]; then
        echo "Sound inactive"
        return
    fi
    vol=${vol%\%}
    if [[ $mute == *yes* ]]; then
        icon=$muted
    elif ((vol <= 0)); then
        icon=$low
    elif ((vol <= 50)); then
        icon=$med
    else
        icon=$high
    fi
    echo "$icon ${vol}%"
}

print_volume
pactl subscribe | stdbuf -oL grep -E "'change' on (sink|server)" |
    while read -r _; do print_volume; done
