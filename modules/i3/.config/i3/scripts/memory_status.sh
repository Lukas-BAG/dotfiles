#!/usr/bin/env bash
# i3blocks memory block: used/total RAM in GiB and percent, from /proc/meminfo.
# Uses MemAvailable, so reclaimable cache doesn't count as used.
# Keeps the block's configured color below 70%, then yellow, orange, red.

set -euo pipefail

awk '
/^MemTotal:/     { total = $2 }
/^MemAvailable:/ { avail = $2 }
END {
    used = total - avail
    pct = total > 0 ? used / total * 100 : 0
    printf "MEM %.1fG/%.1fG (%.f%%)\n", used / 1048576, total / 1048576, pct
    printf "MEM %.f%%\n", pct
    if (pct > 90)      print "#FF0000"
    else if (pct > 80) print "#FFAE00"
    else if (pct > 70) print "#FFF600"
}' /proc/meminfo
