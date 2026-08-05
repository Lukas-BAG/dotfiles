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




