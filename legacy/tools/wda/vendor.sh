#!/bin/bash
# Offline import, requires --source LOCAL_CHECKOUT --commit FULL_SHA.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
exec "${WDA_PYTHON:-python3}" "$root/legacy/tools/wda/manage.py" vendor "$@"
