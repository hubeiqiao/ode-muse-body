#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE="${1:-}"
case "$DEVICE" in ipad|iphone) ;; *) echo "Usage: $0 ipad|iphone"; exit 2;; esac
set -a
source "$ROOT/devices/$DEVICE/.env"
set +a
mkdir -p "$HIDAGENT_DATA"
cd "$ROOT/server"
for service in receiver server; do
  pidfile="$HIDAGENT_DATA/$service.pid"
  module=hidagent
  [ "$service" != receiver ] || module=hidagent.receiver
  port="$PORT"
  [ "$service" != receiver ] || port="$MIRROR_PORT"
  state="$("$ROOT/server/.venv/bin/python" "$ROOT/scripts/service-process.py" "$pidfile" "$ROOT/server/.venv/bin/python" "$module" "$port")" && check=0 || check=$?
  case "$check:$state" in
    0:running)
      echo "$DEVICE $service already verified on port $port"
      continue ;;
    3:absent) ;;  # stale/recycled PID is not a live service
    *) echo "$DEVICE $service ownership is $state; no duplicate started" >&2; exit 1 ;;
  esac
  nohup "$ROOT/server/.venv/bin/python" -u -m "$module" >> "$HIDAGENT_DATA/$service.log" 2>&1 < /dev/null &
  echo $! > "$pidfile"
  echo "$DEVICE $service started (PID $!)"
done

# Keep a supervising shell alive for terminal sessions. Ctrl-C stops this device.
trap 'kill $(jobs -pr) 2>/dev/null || true' EXIT INT TERM
wait
