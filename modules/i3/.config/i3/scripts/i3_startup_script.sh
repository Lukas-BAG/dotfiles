#!/bin/bash
# No strict mode: a launcher that is missing or fails must not stop the rest
# of the session startup.

if [ -f "$HOME/.config/dotfiles/system_local/monitors.sh" ]; then
    . "$HOME/.config/dotfiles/system_local/monitors.sh"
fi

autokey-gtk &

# firefox &

tilix -e bash -c "tmux a || tmux" &

"$HOME/.config/dotfiles/scripts/keyboard-remap.sh" || true

xbindkeys

copyq &
( sleep 2 && copyq config maxitems 1 ) &

flatpak run org.mozilla.firefox &
flatpak run md.obsidian.Obsidian &
flatpak run com.bitwarden.desktop &
nm-applet &

# [ -n "$DISPLAY" ]                     && tmux set-environment -g DISPLAY "$DISPLAY"
# [ -n "$WAYLAND_DISPLAY" ]            && tmux set-environment -g WAYLAND_DISPLAY "$WAYLAND_DISPLAY"
# [ -n "$XDG_SESSION_TYPE" ]           && tmux set-environment -g XDG_SESSION_TYPE "$XDG_SESSION_TYPE"
# [ -n "$DBUS_SESSION_BUS_ADDRESS" ]   && tmux set-environment -g DBUS_SESSION_BUS_ADDRESS "$DBUS_SESSION_BUS_ADDRESS"
# exec /usr/bin/tmux "$@"

# ~/Main/Scripts/Startup_Script/i3_window_change_subscriber.sh &

# seperate Super key from hyper key by making hyper key be mod3 instead of mod4
# this is important because I remapped my RAlt to be the hyper key instead
# and on i3 suddenly some key combinations overlap with the super key
# xmodmap -e "remove mod4 = Hyper_L"
# xmodmap -e "add mod3 = Hyper_L"
