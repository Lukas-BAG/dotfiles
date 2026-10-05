#!/usr/bin/env bash
# No strict mode: tolerates missing or unreadable sysfs files; the exit code
# is meaningful to i3blocks (33 = urgent).
# i3blocks battery block: level + charge state, colored by level.
# Warning color at <= 15%, red and urgent (exit 33) at <= 5%.
# Prints nothing when there's no battery, which hides the block.
# Thresholds match battery-warn (services module).

low=${BATTERY_WARN_LOW:-15}
critical=${BATTERY_WARN_CRITICAL:-5}
supply_dir=${BATTERY_WARN_SUPPLY_DIR:-/sys/class/power_supply}

shopt -s nullglob
total=0 count=0 state=
for bat in "$supply_dir"/BAT*; do
    read -r cap < "$bat/capacity" 2>/dev/null || continue
    read -r status < "$bat/status" 2>/dev/null
    total=$((total + cap)) count=$((count + 1))
    [[ -z $state || $status == Discharging ]] && state=$status
done
[[ $count -eq 0 ]] && exit 0
capacity=$((total / count))

# Glyphs are FontAwesome private-use codepoints; the bar's default font does
# not cover them, so name the font explicitly (needs markup=pango). Glyphs
# are written as UTF-8 bytes so the output doesn't depend on the locale.
fa() { printf "<span font='FontAwesome'>%s</span>" "$1"; }
case $state in
    Discharging) icon=$(fa $'\xef\x89\x80') ;;                   # battery
    Charging)    icon="$(fa $'\xef\x83\xa7') $(fa $'\xef\x87\xa6')" ;; # bolt + plug
    *)           icon=$(fa $'\xef\x87\xa6') ;;                   # plug (full / not charging)
esac

echo "$icon ${capacity}%"
echo "${capacity}%"
if (( capacity <= critical )); then
    echo "#FB4934"
    exit 33
elif (( capacity <= low )); then
    echo "#FABD2F"
else
    echo "#EBDBB2"
fi
