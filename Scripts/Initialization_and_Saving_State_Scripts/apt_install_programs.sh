# Packages needed just to get the repo checked out and stowed.
essential_packages=(
  git
  stow
)

# Everything else this dotfiles setup expects to be available.
additional_packages=(
  i3
  tilix
  tmux
  progress
  i3blocks
  feh
  vim-gtk3
  rofi
  sway
  ranger
  xclip
  rsync
  gparted
  xinput
  picom
  gcc
  fonts-font-awesome
  imagemagick
  pulseaudio-utils
  brightnessctl
  network-manager-gnome
  libnotify-bin
  ffmpeg
  curl
  git-credential-oauth
  ripgrep
  make
  fzf
  copyq
  trash-cli
  jq
  pandoc
  inotify-tools
)

# Usage: apt_install_programs.sh [essential|additional|all]  (default: all)
mode="${1:-all}"
case "$mode" in
  essential) packages=("${essential_packages[@]}") ;;
  additional) packages=("${additional_packages[@]}") ;;
  all) packages=("${essential_packages[@]}" "${additional_packages[@]}") ;;
  *) echo "Usage: $0 [essential|additional|all]" >&2; exit 1 ;;
esac

sudo apt -y install "${packages[@]}"
