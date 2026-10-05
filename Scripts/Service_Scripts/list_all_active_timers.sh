#!/bin/bash
set -euo pipefail
sudo systemctl list-timers
systemctl --user list-timers
