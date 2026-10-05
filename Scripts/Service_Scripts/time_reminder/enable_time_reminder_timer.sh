#!/bin/bash
set -euo pipefail
systemctl --user enable --now time_reminder.timer
