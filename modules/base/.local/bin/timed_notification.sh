#!/bin/bash
# set -e/-u only, no pipefail: head closes the /dev/urandom pipe early (SIGPIPE).
set -eu

function usage {
    echo "Timed Notification script"
    echo ""
    echo "Will send yourself a notification in a specified amount of seconds"
    echo ""
    echo "$0 <time_in_seconds_for_the_timer> <notification_to_send>"
}

if [ "${1:-}" == "-h" ]; then
    usage
    exit 0
fi

if [ "${1:-}" == "--help" ]; then
    usage
    exit 0
fi

if [ "$#" -lt 2 ]; then
    echo "Wrong number of arguments"
    usage
    exit 1
fi

# Two random characters as the timer name (first get random byte stream, then filter for only alphabet characters, then fold the stream every 2 letters and lastly choose the first option)
timer_name=$(cat /dev/urandom | tr -dc 'A-Z' | fold -w 2 | head -n 1)

echo "$(date "+%d.%m.%Y %H:%M:%S") - Starting a $1 second timer (name: $timer_name) with message:" >> ~/.notification_log
printf "\t$2\n" >> ~/.notification_log
( sleep $1; notify-send "$1 second timer is up" "$2"; printf "$1 second timer (name: $timer_name) finished\n\n\n" >> ~/.notification_log ) &
