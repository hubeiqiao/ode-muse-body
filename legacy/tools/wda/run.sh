#!/bin/bash
# Explicit live entry point. Never invoked by build.sh or the offline test suite.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
exec "${WDA_PYTHON:-python3}" "$root/legacy/tools/wda/manage.py" run "$@"
