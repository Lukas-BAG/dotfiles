#!/usr/bin/env bash
# i3blocks CPU block: total usage over a one second sample, from /proc/stat.
# Yellow at >= 50%, red at >= 80%.

set -euo pipefail

sample() {
    local _ user nice system idle_t iowait irq softirq steal
    read -r _ user nice system idle_t iowait irq softirq steal _ < /proc/stat
    total=$((user + nice + system + idle_t + iowait + irq + softirq + steal))
    idle=$((idle_t + iowait))
}

sample; total1=$total idle1=$idle
sleep 1
sample

usage=$(awk -v t=$((total - total1)) -v i=$((idle - idle1)) \
    'BEGIN { printf "%.2f", (t > 0) ? 100 * (t - i) / t : 0 }')

echo "CPU $usage%"
echo "CPU $usage%"
if awk -v u="$usage" 'BEGIN { exit !(u >= 80) }'; then
    echo "#FF0000"
elif awk -v u="$usage" 'BEGIN { exit !(u >= 50) }'; then
    echo "#FFFC00"
else
    echo "#EBDBB2"
fi
