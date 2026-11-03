#!/bin/bash
# Generic-device build only. Exact xcodebuild arguments: manage.py build_command.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
exec "${WDA_PYTHON:-python3}" "$root/legacy/tools/wda/manage.py" build "$@"
