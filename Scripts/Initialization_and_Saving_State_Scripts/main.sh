#!/bin/bash
set -euo pipefail
# Set color scheme (for gnome things at least) to dark mode
gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'
. ./apt_install_programs.sh
. ./load_tilix_config.sh
. ./init_bashmarks.sh
