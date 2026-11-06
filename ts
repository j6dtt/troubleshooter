#!/bin/sh
# Chat: ./ts   Test connections: ./ts check   Guide: ./ts --help
# Uses your kubectl logins: one kubectl proxy per context runs on this host, as
# you, only until this command ends. The container reaches them on 127.0.0.1.
cd "$(dirname "$0")" || exit 1
case "${1:-}" in
  -h|--help|help) cat src/troubleshooter/help.txt; exit 0 ;;
esac
proxies=""
current=""
pids=""
dir=""
tmp=""
# If you stop learn, stop it in the container too, or it carries on using the model.
stop_learn() {
  [ -z "$tmp" ] || docker compose exec -T troubleshooter sh -c \
    "kill \$(cat /tmp/learn.$$) 2>/dev/null; rm -f /tmp/learn.$$" 2>/dev/null
}
trap 'stop_learn; kill $pids 2>/dev/null; rm -rf "$dir" "$tmp"' EXIT
trap 'exit 130' INT TERM

if command -v kubectl >/dev/null 2>&1; then
  contexts=$(kubectl config view -o 'jsonpath={range .contexts[*]}{.name}{"\t"}{.context.namespace}{"\n"}{end}' 2>/dev/null)
fi
if [ -n "${contexts:-}" ]; then
  current=$(kubectl config current-context 2>/dev/null || true)
  # In the foreground first: a login plugin may need to ask for a password.
  if [ -n "$current" ] && ! kubectl get --raw /version >/dev/null; then
    echo "Not logged in to $current. Log in from another terminal; no need to restart."
  fi
  dir=$(mktemp -d)
  n=0
  while IFS="$(printf '\t')" read -r context namespace; do
    n=$((n + 1))
    kubectl proxy --context "$context" --port=0 --address=127.0.0.1 >"$dir/$n" 2>&1 </dev/null &
    pid=$!
    pids="$pids $pid"
    tries=0
    until port=$(sed -n 's/.*127\.0\.0\.1:\([0-9]*\).*/\1/p' "$dir/$n") && [ -n "$port" ]; do
      tries=$((tries + 1))
      if [ "$tries" -ge 50 ] || ! kill -0 "$pid" 2>/dev/null; then
        echo "Skipping context $context: its kubectl proxy did not start."
        port=""
        break
      fi
      sleep 0.1
    done
    [ -z "$port" ] || proxies="$proxies$context|$port|$namespace;"
  done <<EOF
$contexts
EOF
fi

if [ "${1:-}" = learn ]; then
  # The container only prints the runbook. Saving it here, as you, works on a share
  # where the container's root can't write. One folder per host: /apps differs by host.
  target=""
  for arg in "$@"; do
    case "$arg" in /*) target=$arg ;; esac
  done
  safe() { printf %s "$1" | tr -c 'A-Za-z0-9._-' '-'; }
  host=$(safe "$(uname -n)")
  app=$(safe "$(basename "${target:-none}")")
  mkdir -p "runbooks/$host" || { echo "Cannot write to ./runbooks/$host"; exit 1; }
  tmp="runbooks/$host/.$app.$$"
  docker compose exec -T -e "LEARN_RUN=$$" -e "KUBE_PROXIES=$proxies" -e "KUBE_CURRENT=$current" \
    troubleshooter ts "$@" >"$tmp"
  code=$?
  if [ "$code" -ne 0 ] || [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    tmp=""
    exit "$code"
  fi
  file="runbooks/$host/$app.md"
  [ ! -f "$file" ] || cp -p "$file" "$file.bak"
  chmod 644 "$tmp"
  chmod 755 "runbooks/$host"
  mv "$tmp" "$file"
  tmp=""
  echo "Saved $file"
  exit 0
fi

docker compose exec -e "KUBE_PROXIES=$proxies" -e "KUBE_CURRENT=$current" troubleshooter ts "$@"
