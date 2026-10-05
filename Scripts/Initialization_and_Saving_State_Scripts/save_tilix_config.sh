#!/bin/bash
set -euo pipefail
dconf dump /com/gexperts/Tilix/ > ./configs_saved_states_etc/tilix_dconf
