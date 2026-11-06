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
  pids=""
  trap 'kill $pids 2>/dev/null; rm -rf "$dir"' EXIT
  trap 'exit 130' INT TERM
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

docker compose exec -e "KUBE_PROXIES=$proxies" -e "KUBE_CURRENT=$current" troubleshooter ts "$@"
