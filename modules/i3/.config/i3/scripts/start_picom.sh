#!/bin/bash
set -euo pipefail
if systemd-detect-virt -q; then
    picom --backend xrender
else
    picom --backend glx
fi
