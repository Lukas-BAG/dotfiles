# Adds default bashmarks to ~/.sdirs, skipping any that are already defined.
_add_bashmark() {
    local name="$1"
    local path="$2"
    if ! grep -q "DIR_${name}=" "$HOME/.sdirs" 2>/dev/null; then
        echo "export DIR_${name}=\"${path}\"" >> "$HOME/.sdirs"
    fi
}

_add_bashmark main        '$HOME/Main'
_add_bashmark downloads   '$HOME/Downloads'
_add_bashmark tools       '$HOME/Main/Tools'
_add_bashmark dotfiles    '$HOME/Main/dotfilesv3'
_add_bashmark nvim        '$HOME/Main/dotfilesv3/modules/nvim/.config/nvim'
_add_bashmark scripts     '$HOME/Main/Scripts'
_add_bashmark e           '$HOME/Main/Everything'
_add_bashmark everything  '$HOME/Main/Everything'
_add_bashmark config      '$HOME/.config'
_add_bashmark i3          '$HOME/.config/i3'
_add_bashmark logs        '/var/log'
_add_bashmark applications '/usr/share/applications'
_add_bashmark claude      '$HOME/.claude'
_add_bashmark temp        '$HOME/Main/Everything/temp'

unset -f _add_bashmark
