#!/bin/bash
set -euo pipefail
: "${WDA_DEVICE_UDID:?Set WDA_DEVICE_UDID to the explicitly selected iPad mini UDID}"
root="$(cd "$(dirname "$0")/../../.." && pwd)"
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
exec "${WDA_PYTHON:-python3}" "$root/legacy/tools/handsy-runner/manage.py" build
