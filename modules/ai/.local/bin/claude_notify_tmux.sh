#!/bin/bash
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

if [ -n "$TMUX_PANE" ]; then
    pane_tty=$(tmux display-message -p -t "$TMUX_PANE" '#{pane_tty}' 2>/dev/null)
    if [ -n "$pane_tty" ]; then
        printf '\a' > "$pane_tty" 2>/dev/null
    fi
fi
exit 0
