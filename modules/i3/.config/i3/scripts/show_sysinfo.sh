#!/bin/bash
# No strict mode: every probe is best effort (grep on top output, optional
# claude usage); a failing one must not suppress the notification.
cpu=$(top -bn1 | grep "Cpu(s)" | awk '{print 100 - $8}')
ram=$(free -h | awk '/^Mem:/ {print $3 "/" $2}')
disk=$(df -h / | awk 'NR==2 {print $3 "/" $2 " (" $5 " used)"}')
uptime=$(uptime -p | sed 's/^up //')
date=$(date '+%d.%m.%Y')

body="Date:   ${date}
─────────────────
CPU:    ${cpu}%
RAM:    ${ram}
Disk:   ${disk}
Uptime: ${uptime}"

if [ -x "$HOME/.local/bin/claude_show_usage.sh" ]; then
    claude_usage=$("$HOME/.local/bin/claude_show_usage.sh" 2>/dev/null)
    if [ -n "$claude_usage" ]; then
        five_hour=$(echo "$claude_usage" | sed 's/ | .*//')
        seven_day=$(echo "$claude_usage" | sed 's/.* | //')
        body="${body}
─────────────────
Claude:
  ${five_hour}
  ${seven_day}"
    fi
fi

notify-send "System Info" "$body"
