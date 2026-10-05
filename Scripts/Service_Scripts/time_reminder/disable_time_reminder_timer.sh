#!/bin/bash
set -euo pipefail
systemctl --user disable --now time_reminder.timer
