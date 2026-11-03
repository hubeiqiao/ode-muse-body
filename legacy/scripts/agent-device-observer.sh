#!/bin/bash
# Native observation over the already-paired Wi-Fi device; normal input stays on ESP32.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$ROOT/legacy/tools/agent-device/node_modules/.bin/agent-device"
if [ -f "$ROOT/devices/iphone/agent-device-signing.env" ]; then
  set -a
  source "$ROOT/devices/iphone/agent-device-signing.env"
  set +a
fi
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
export AGENT_DEVICE_HOME="$ROOT/runtime/iphone/agent-device/home"
export AGENT_DEVICE_STATE_DIR="$ROOT/runtime/iphone/agent-device/state"
export AGENT_DEVICE_NO_UPDATE_NOTIFIER=1
export AGENT_DEVICE_IOS_BUNDLE_ID="${AGENT_DEVICE_IOS_BUNDLE_ID:-com.example.esp32observer}"
export AGENT_DEVICE_IOS_RUNNER_DERIVED_PATH="$ROOT/legacy/tools/agent-device/node_modules/agent-device/.tmp/esp32-observer/derived"
TARGET_UDID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["udid"])' "$ROOT/runtime/iphone/agent-device/device.json")"
SCOPED=(--platform ios --udid "$TARGET_UDID" --session iphone-native-observer)
case "${1:-}" in
  --version|help) exec "$CLI" "$@" ;;
  devices|doctor|prepare) exec "$CLI" "$@" "${SCOPED[@]}" ;;
  snapshot) exec "$CLI" "$@" "${SCOPED[@]}" --observe-only ;;
  bind)
    # One setup activation to associate an already-installed app with the reader.
    # This is not exposed as a normal phone-input tool.
    [ "$#" -eq 2 ] && [[ "$2" =~ ^[A-Za-z0-9]+([.][A-Za-z0-9_-]+)+$ ]] || { echo 'Usage: agent-device-observer.sh bind <installed.bundle.id>' >&2; exit 2; }
    exec "$CLI" open "$2" "${SCOPED[@]}" --json ;;
  *) echo 'Observation-only wrapper: help, devices, doctor, prepare ios-runner, bind, snapshot. Click/type/swipe input belongs to ESP32.' >&2; exit 2 ;;
esac
