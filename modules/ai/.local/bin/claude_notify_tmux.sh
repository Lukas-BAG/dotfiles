#!/bin/bash
# No strict mode: a best-effort hook that must always exit 0, also outside
# tmux ($TMUX_PANE unset) and without a tty.
#
# Emit a terminal bell into the invoking tmux pane so that tmux's
# bell-monitoring flags the window when Claude finishes responding or
# is waiting on the user (permission prompt / idle).
#
# Wired up as the Stop and Notification hooks in .claude/settings.json.
#
# Claude Code captures hooks' stdout rather than passing it through to
# the terminal, and the hook subprocess has no controlling tty of its
# own (/dev/tty is unavailable), so the bell is written directly to the
# invoking pane's pty device, resolved via tmux itself.
#
# Claude Code passes the hook payload as JSON on stdin. A Notification with
# notification_type "idle_prompt" ("Claude is waiting for your input", sent
# after about a minute of idleness) is skipped: the Stop bell already flagged
# the window, and ringing again would flag it with nothing new to look at.
# Matched with grep so jq is not required; empty or invalid stdin just rings.

payload=""
[ -t 0 ] || payload=$(cat)
if printf '%s' "$payload" | grep -Eq '"notification_type"[[:space:]]*:[[:space:]]*"idle_prompt"'; then
    exit 0
fi

if [ -n "$TMUX_PANE" ]; then
    pane_tty=$(tmux display-message -p -t "$TMUX_PANE" '#{pane_tty}' 2>/dev/null)
    if [ -n "$pane_tty" ]; then
        printf '\a' > "$pane_tty" 2>/dev/null
    fi
fi
exit 0
