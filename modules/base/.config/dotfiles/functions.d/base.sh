#!/bin/sh


###################### Most useful things ######################


# Fuzzy-pick a bashmark (from ~/.sdirs) and open nvim there
proj() {
  if [ "$#" -ge 2 ]; then
    echo "proj: too many arguments" >&2
    return 1
  fi

  local entries
  entries=$(grep '^export DIR_' ~/.sdirs \
    | sed 's/export DIR_\([^=]*\)="\(.*\)"/\1\t\2/')

  if [ "$#" -eq 1 ]; then
    local exact
    exact=$(echo "$entries" | awk -F'\t' -v name="$1" '$1 == name {print $2}')
    if [ -n "$exact" ]; then
      cd "$(eval echo "$exact")" && nvim
      return
    fi
  fi

  local dir
  dir=$(echo "$entries" \
    | fzf --with-nth=1 ${1:+--query="$1"} \
    | cut -f2)
  [ -n "$dir" ] && cd "$(eval echo "$dir")" && nvim
}

_proj_complete() {
  local cur="${COMP_WORDS[COMP_CWORD]}"
  local bookmarks
  bookmarks=$(grep '^export DIR_' ~/.sdirs | sed 's/export DIR_\([^=]*\)=.*/\1/')
  COMPREPLY=($(compgen -W "$bookmarks" -- "$cur"))
}
complete -F _proj_complete proj


# Save the current working directory into a variable as a name so you can later cd $var_name to get back to it
# you can achieve this same behavior with the bashmarks extension though those paths last for more than one session
sd() {
    eval "$1='$(pwd)'"
}


DID_I_FUCKING_STUTTER() {
    local last_command
    last_command=$(fc -ln -1)
    sudo bash -c "$last_command"
}


myMount() {
    sudo mount "$1" /mnt/usb/
    cd /mnt/usb/
}

myUmount() {
    sudo umount /mnt/usb/
    sync
}

myCryptMount() {
    sudo cryptsetup open "$1" myusb
    sudo mount /dev/mapper/myusb /mnt/usb/
    cd /mnt/usb/
}

myCryptUmount() {
    sudo umount /mnt/usb/
    sudo cryptsetup close myusb
    sync
}


# Shutdown function (with help from ChatGPT)
# Shuts down the pc and you can specify the amount of seconds it waits before it shuts down with argument 1
# If no argument is given it will default to some amount of seconds (currently 6)
# You can press Ctrl C to cancel or close the terminal in which this script is executed (unless the process persists like in tmux)
myShutdown() {
    secs=${1:-30}
    while [ $secs -gt 0 ]; do
        echo -ne "$secs Seconds till shut down (type 'now' to skip waiting)\033[0K\r"
	sleep 1
        if read -t 1 -n 3 input; then
            # Check if the input is 'now'
            if [[ $input == "now" ]]; then
                echo -ne "\nShutting down now!\n"
                systemctl poweroff
            fi
        fi
	: $((secs--))
    done
    systemctl poweroff
}

myReboot() {
    secs=${1:-30}
    while [ $secs -gt 0 ]; do
        echo -ne "$secs Seconds till reboot (type 'now' to skip waiting)\033[0K\r"
	sleep 1
        if read -t 1 -n 3 input; then
            # Check if the input is 'now'
            if [[ $input == "now" ]]; then
                echo -ne "\nRebooting now!\n"
                systemctl reboot
            fi
        fi
	: $((secs--))
    done
    systemctl reboot
}

myHibernate() {
    # sudo echo ""
    mem_str="$(free -h | sed -n '2 p' | awk '{print $7}')"
    mem=${mem_str%Gi} # split the Gi from the number of free gigs
    # TODO right now disabling this check by just setting mem manually to 16
    mem=16

    if [ "$(echo "$mem >= 8" | bc -l)" -eq 1 ]; then
        # echo 0 | sudo tee /sys/power/image_size
        systemctl hibernate
    else
        echo "Warning: less than 8 Gigs of RAM available, hibernation could fail" && free -h && return 1
    fi
}






# TODO: move this to a better spot
prettyDf() { # Call df for viewing remaining and total space on harddisk but present it in an easily viewable way
    echo "Remaining Space on disk (might not be 100% accurate)"
    echo ""
    df -h --total | head -n 1
    df -h --total | tail -n 1
}


cw () {
    cd $1
    ls
}

myHibernateDropCachesIfTooLittleRAM() {
    sudo echo ""
    if ! myHibernate; then
        myDropCaches
        sleep 1
        myHibernate
    fi
}


# Testing this function
myDropCaches() {
    sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'
}


myVeracryptMount() {
    if [ "$#" -ne 2 ]; then
        echo "$0 - 2 arguments required, container and mount point"
        return 1
    fi
    sudo veracrypt --text --pim=0 --keyfiles="" --protect-hidden=no --mount "$1" "$2" && cd "$2"
}

myVeracryptDismount() {
    sudo veracrypt --dismount
}
alias myVeracryptUmount=myVeracryptDismount

myVeracryptCreate() {
    if [ "$#" -ne 1 ]; then
        echo "$0 - 1 argument required, container name"
        return 1
    fi
    sudo veracrypt --text --create "$1"
}


mkcdir () {
    if [ "$#" -ne 1 ]; then
        echo "mkcdir - Error: 1 argument required, directory name"
        return 1
    fi
    mkdir -p -- "$1" &&
    builtin cd -P -- "$1"
}

prefix() {
    mv -i "$1" "$2$1"
}

# Show current dir's contents sorted by size (human readable).
# If an entry has a sidecar note file named "<entry>_<something>.md",
# show that note's name (minus .md) instead of the raw entry name.
duh() {
    du -h --max-depth=1 |
    sort -hr |
    while IFS=$'\t' read -r size path; do
        prefix=${path#./}

        if [ "$prefix" = "." ]; then
            printf '%s\t%s\n' "$size" "$path"
            continue
        fi

        sidecar=$(find . -maxdepth 1 -type f -name "${prefix}_*.md" -print -quit)

        if [ -n "$sidecar" ]; then
            name=${sidecar#./}
            name=${name%.md}
            printf '%s\t%s\n' "$size" "$name"
        else
            printf '%s\t%s\n' "$size" "$prefix"
        fi
    done
}

########################################################


entry() {
    if [ "$(date +%Y)" != "2026" ]; then
        echo "entry: only works in 2026, update the function for the new year." >&2
        return 1
    fi

    local everything_dir="$HOME/Main/Everything"
    local env_id="wsl"

    local last_id
    last_id=$(ls "$everything_dir" | grep -E "^[0-9]+-${env_id}$" | sort | tail -1 | sed "s/-${env_id}$//")
    local next_id=$(( ${last_id:-260000} + 1 ))

    if [ -z "$1" ]; then
        echo "Usage: entry <name>" >&2
        return 1
    fi

    local name_slug
    name_slug=$(echo "$1" | tr '[:upper:]' '[:lower:]' | tr ' ' '_')

    local dir_name="${next_id}-${env_id}"
    mkdir -p "$everything_dir/$dir_name"
    touch "$everything_dir/${dir_name}_${name_slug}.md"

    cd "$everything_dir/$dir_name"
    echo "Created $dir_name — $1"
}


########################################################


sshAddKey() {
    [ -f ~/.ssh/id_ed25519 ] || { echo "sshAddKey: key not found at ~/.ssh/id_ed25519" >&2; return 1; }
    ssh-add -l >/dev/null 2>&1; [ $? -eq 2 ] && eval "$(ssh-agent -s)"
    local fingerprint
    fingerprint=$(ssh-keygen -lf ~/.ssh/id_ed25519 | awk '{print $2}')
    ssh-add -l | grep -qF "$fingerprint" || ssh-add ~/.ssh/id_ed25519
}


########################################################


getClaudeSettings() {
    local target=".claude/settings.json"
    local raw_url="https://raw.githubusercontent.com/Lukas-BAG/claudeSettings/main/.claude/settings.json"

    if [ -f "$target" ]; then
        echo "getClaudeSettings: $target already exists, aborting." >&2
        return 1
    fi

    mkdir -p ".claude"
    if ! curl -fsSL "$raw_url" -o "$target"; then
        echo "getClaudeSettings: failed to fetch settings from GitHub." >&2
        return 1
    fi

    echo "getClaudeSettings: wrote $target"
}


########################################################


# Render a markdown file (with Mermaid diagrams) in the Windows browser.
# With -w/--watch, regenerates on file change and auto-refreshes the browser.
mdview() {
  local src html watch=false port srv_pid port_file

  for arg in "$@"; do
    case "$arg" in
      -w|--watch) watch=true ;;
      -*) echo "mdview: unknown option $arg" >&2; return 1 ;;
      *) src="$arg" ;;
    esac
  done

  if [[ -z "$src" ]]; then
    echo "Usage: mdview [-w|--watch] <file.md>" >&2
    return 1
  fi

  src=$(realpath "$src")
  html=$(mktemp --suffix=.html)

  _mdview_render() {
    local tmp
    tmp=$(mktemp --suffix=.html)
    pandoc "$src" --from=gfm --to=html5 --standalone \
      --metadata title="$(basename "$src" .md)" -o "$tmp"
    local inject='<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script><script>document.querySelectorAll("pre.mermaid").forEach(el=>{const d=document.createElement("div");d.className="mermaid";d.textContent=el.textContent;el.replaceWith(d)});mermaid.initialize({startOnLoad:true});</script>'
    if $watch; then
      inject+="<script>new EventSource('http://localhost:${port}/events').onmessage=()=>location.reload(true)</script>"
    fi
    sed -i "s|</body>|${inject}</body>|" "$tmp"
    mv "$tmp" "$html"
  }

  if $watch; then
    port_file=$(mktemp)
    python3 - "$html" "$port_file" >/dev/null 2>&1 <<'PYEOF' &
import sys,os,time,threading,socket,http.server,socketserver
html=sys.argv[1];pf=sys.argv[2]
s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
clients=[];lock=threading.Lock()
def watcher():
 try:mt=os.path.getmtime(html)
 except:mt=0
 while True:
  time.sleep(0.3)
  try:
   t=os.path.getmtime(html)
   if t!=mt:
    mt=t
    with lock:
     for q in clients[:]:q.append(1)
  except:pass
class H(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  if self.path=='/events':
   self.send_response(200)
   self.send_header('Content-Type','text/event-stream')
   self.send_header('Cache-Control','no-cache')
   self.end_headers()
   q=[]
   with lock:clients.append(q)
   try:
    while True:
     if q:del q[:];self.wfile.write(b'data:r\n\n')
     else:self.wfile.write(b':k\n\n')
     self.wfile.flush();time.sleep(0.5)
   except:
    with lock:
     if q in clients:clients.remove(q)
  else:
   try:
    d=open(html,'rb').read()
    self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',len(d));self.end_headers();self.wfile.write(d)
   except:self.send_response(404);self.end_headers()
class TS(socketserver.ThreadingMixIn,http.server.HTTPServer):daemon_threads=True
threading.Thread(target=watcher,daemon=True).start()
srv=TS(('127.0.0.1',port),H)
open(pf,'w').write(str(port))
srv.serve_forever()
PYEOF
    srv_pid=$!

    local i
    for i in $(seq 20); do
      sleep 0.1
      port=$(cat "$port_file" 2>/dev/null)
      [[ -n "$port" ]] && break
    done
    rm -f "$port_file"

    if [[ -z "$port" ]]; then
      echo "mdview: failed to start preview server" >&2
      kill "$srv_pid" 2>/dev/null
      rm -f "$html"
      return 1
    fi

    _mdview_render
    explorer.exe "http://localhost:$port"
    echo "mdview: watching $(basename "$src") at http://localhost:$port — Ctrl+C to stop"

    trap "kill $srv_pid 2>/dev/null; rm -f $html; trap - INT" INT

    if command -v inotifywait &>/dev/null; then
      while inotifywait -qq -e close_write,moved_to "$src" 2>/dev/null; do
        _mdview_render
      done
    else
      local prev curr
      prev=$(stat -c %Y "$src")
      while sleep 1; do
        curr=$(stat -c %Y "$src")
        if [[ "$curr" != "$prev" ]]; then
          prev=$curr
          _mdview_render
        fi
      done
    fi

    kill "$srv_pid" 2>/dev/null
    rm -f "$html"
    trap - INT
  else
    _mdview_render
    explorer.exe "$(wslpath -w "$html")"
  fi
}


########################################################

# Quick-create the next numbered location in an "Everything" dir
# (~/Main/Everything/-style: entries named "<yy><seq>[-<suffix>]" plus a
# matching empty sidecar file "<entry>_<snake_case_description>[_@tag...].md",
# see duh() above).
#
# Must be run from inside an existing Everything dir. On first use in a given
# dir it asks once which suffix to use there (can be left blank) and
# remembers it in a hidden ".mynew-suffix" file in that dir from then on.
#
# Usage: mynew "description" [tag ...]
#   tags may be passed with or without a leading "@" (e.g. "ai" or "@ai").
mynew() {
    local suffix_file=".mynew-suffix"
    local entry_re='^[0-9]{6}(-[A-Za-z0-9]+)?$'

    if [ "$#" -lt 1 ]; then
        echo "mynew: 1 argument required, description (plus optional tags)" >&2
        return 1
    fi

    local description="$1"
    shift

    local entry max_id=0
    for entry in */; do
        entry=${entry%/}
        if [[ "$entry" =~ $entry_re ]]; then
            local id=$((10#${entry:0:6}))
            [ "$id" -gt "$max_id" ] && max_id=$id
        fi
    done

    if [ "$max_id" -eq 0 ]; then
        echo "mynew: no existing <yy><seq>[-suffix] entries found in $(pwd) - this doesn't look like an Everything dir, refusing" >&2
        return 1
    fi

    local suffix
    if [ ! -e "$suffix_file" ]; then
        echo "mynew: no suffix configured for $(pwd) yet."
        read -r -p "Suffix to use for new entries here (leave blank for none): " suffix
        printf '%s' "$suffix" > "$suffix_file"
    else
        suffix=$(cat "$suffix_file")
    fi

    local current_year max_year max_seq next_seq
    current_year=$(date +%y)
    max_year=${max_id:0:2}
    max_seq=$((10#${max_id:2:4}))

    if [ "$max_year" = "$current_year" ]; then
        next_seq=$((max_seq + 1))
    else
        echo "mynew: year prefix rolled over (highest existing entry is '$max_year', current year is '$current_year') - starting sequence over at 0001"
        next_seq=1
    fi

    local new_id
    new_id=$(printf '%s%04d' "$current_year" "$next_seq")

    local new_dirname="$new_id"
    [ -n "$suffix" ] && new_dirname="${new_id}-${suffix}"

    if [ -e "$new_dirname" ]; then
        echo "mynew: '$new_dirname' already exists, refusing to touch it" >&2
        return 1
    fi

    local snake_description
    snake_description=$(echo "$description" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+//; s/_+$//')

    local tag_suffix="" tag
    for tag in "$@"; do
        tag=${tag#@}
        tag_suffix="${tag_suffix}_@${tag}"
    done

    local sidecar="${new_dirname}_${snake_description}${tag_suffix}.md"

    mkdir -- "$new_dirname" &&
    touch -- "$sidecar" &&
    cd -- "$new_dirname" &&
    echo "mynew: created '$new_dirname/' with sidecar '$sidecar'"
}


# Given a path to a "<yy><seq>[-suffix]" entry dir, print the name of its
# sidecar note file (minus ".md"), or the entry's own dirname if it has none.
# See duh() above for the same sidecar convention.
_ge_sidecar_name() {
    local entry="$1"
    local base="${entry%/*}"
    local name="${entry##*/}"
    [ "$base" = "$entry" ] && base="."

    local sidecar
    sidecar=$(find "$base" -maxdepth 1 -type f -name "${name}_*.md" -print -quit)

    if [ -n "$sidecar" ]; then
        sidecar=${sidecar##*/}
        echo "${sidecar%.md}"
    else
        echo "$name"
    fi
}

# Shared "matches -> cd" tail for ge/gel: cd straight in on a single match,
# otherwise offer an fzf picker (or list-and-refuse if fzf isn't installed).
# $1 = label to prefix messages with (e.g. "ge"/"gel"), $2 = human-readable
# description of what was searched for (used in messages only), rest = matches.
_ge_pick_and_cd() {
    local label="$1" reason="$2"
    shift 2
    local matches=("$@")

    case "${#matches[@]}" in
        0)
            echo "$label: no entry matching '$reason' found" >&2
            return 1
            ;;
        1)
            cd -- "${matches[0]}"
            echo "$label: $(_ge_sidecar_name "${matches[0]}")"
            ;;
        *)
            if command -v fzf >/dev/null 2>&1; then
                local pick entry_display=() entry
                for entry in "${matches[@]}"; do
                    entry_display+=("$(_ge_sidecar_name "$entry")"$'\t'"$entry")
                done
                pick=$(printf '%s\n' "${entry_display[@]}" |
                    fzf --with-nth=1 --delimiter=$'\t' --prompt="$label: multiple matches for '$reason' > " |
                    cut -f2)
                if [ -n "$pick" ]; then
                    cd -- "$pick"
                else
                    echo "$label: no selection made, refusing" >&2
                    return 1
                fi
            else
                echo "$label: multiple entries match '$reason', refusing:" >&2
                printf '  %s\n' "${matches[@]}" >&2
                return 1
            fi
            ;;
    esac
}

# Shared implementation for ge/gel: find "<yy><seq>[-suffix]" under $4 and cd
# into it. Errors out if zero or more than one entry matches.
_ge_goto() {
    local label="$1" id="$2" year_arg="$3" base="$4"

    if [ -z "$id" ] || ! [[ "$id" =~ ^[0-9]+$ ]]; then
        echo "usage: id must be numeric, e.g. '$label 1'" >&2
        return 1
    fi

    local year
    if [ -n "$year_arg" ]; then
        if [[ "$year_arg" =~ ^[0-9]{4}$ ]]; then
            year=${year_arg:2:2}
        elif [[ "$year_arg" =~ ^[0-9]{2}$ ]]; then
            year=$year_arg
        else
            echo "invalid year '$year_arg' - use e.g. 25 or 2025" >&2
            return 1
        fi
    else
        year=$(date +%y)
    fi

    if [ ! -d "$base" ]; then
        echo "not a dir: $base" >&2
        return 1
    fi

    local prefix
    prefix=$(printf '%s%04d' "$year" "$id")

    local matches=() entry name
    for entry in "$base"/*/; do
        entry=${entry%/}
        name=${entry##*/}
        [[ "$name" =~ ^${prefix}(-[A-Za-z0-9]+)?$ ]] && matches+=("$entry")
    done

    _ge_pick_and_cd "$label" "$prefix" "${matches[@]}"
}

# String-search variant of _ge_goto: instead of an id, take a substring to
# grep sidecar filenames for (case-insensitive), under $3. A sidecar is named
# "<yy><seq>[-suffix]_<description>[@tags].md" (see mynew() above), so the
# entry dirname is everything before the first "_". cd's straight in on a
# single match, otherwise offers the same fzf picker as _ge_goto.
_ge_goto_string() {
    local label="$1" needle="$2" base="$3"

    if [ ! -d "$base" ]; then
        echo "not a dir: $base" >&2
        return 1
    fi

    local matches=() sidecar filename dirname entry
    for sidecar in "$base"/*_*.md; do
        [ -e "$sidecar" ] || continue
        filename=${sidecar##*/}
        case "${filename,,}" in
            *"${needle,,}"*)
                dirname=${filename%%_*}
                entry="$base/$dirname"
                [ -d "$entry" ] && matches+=("$entry")
                ;;
        esac
    done

    _ge_pick_and_cd "$label" "$needle" "${matches[@]}"
}

# g (bashmarks) + e (Everything): thin wrapper that jumps to the bashmark
# "e" (see bashmarks.sh) and then hands off to gel, i.e. it's just
# "g e && gel <whatever you gave it>". See gel() below for what it accepts
# (numeric id, optionally with year, or free text searched against sidecars).
#
# Usage: ge <id> [year]
#        ge <text>
#   ge 1        -> ~/Main/Everything/<currentyear>0001[-suffix]
#   ge 1 25     -> .../250001[-suffix]
#   ge 1 2025   -> .../250001[-suffix]
#   ge fire     -> greps sidecars for "fire"
ge() {
    local sdirs="${SDIRS:-$HOME/.sdirs}"
    [ -f "$sdirs" ] && source "$sdirs"

    if [ ! -d "$DIR_e" ]; then
        echo "ge: bashmark 'e' is not set to a valid dir (set it with: s e)" >&2
        return 1
    fi

    g e && gel "$@"
}

# Same as ge, but searches the current directory instead of jumping to the
# "e" bashmark first - handy when you're already inside an Everything dir.
#
# Accepts either a numeric id (like ge) or free text: if the argument is
# all-digits it's treated as an id (optionally with a year in $2), otherwise
# it's grepped against sidecar filenames (see _ge_goto_string()) and you land
# straight in the single match, or get the usual fzf picker on multiple.
#
# Usage: gel <id> [year]
#        gel <text>
gel() {
    if [ -z "$1" ]; then
        echo "usage: gel <id-or-text> [year]" >&2
        return 1
    fi

    if [[ "$1" =~ ^[0-9]+$ ]]; then
        _ge_goto "gel" "$1" "$2" "$(pwd)"
    else
        _ge_goto_string "gel" "$1" "$(pwd)"
    fi
}


################## Cut copy and paste functions ########

# used for quickly cutting, copying and pasting files
# use cut or copy command to mark a file then go to
# a different dir and use the paste command to move or
# copy the input there
# Currently (07.07.2024) using the commands
# myCut   / mycut   / c
# myCopy  / mycopy  / y
# myPaste / mypaste / p
# source ~/Main/Scripts/CutCopyPaste/cutcopypaste_aliases.sh

########################################################


##### Mark directory and then move there functions #####

# TODO implement this again. I did have this but then I
#      deleted the script by accident
# Sort of the reverse of my custom cut copy and paste
# commands
# because with the cut copy and paste commands you first
# select a file to be cut or copied and then call the
# paste function in the target directory
#
# with the functions defined in here you mark a directory
# as the target directory then you can call functions to
# move or copy files and directories to that place
# source ~/.myTargetDirThenMoveFilesThere.sh

########################################################




