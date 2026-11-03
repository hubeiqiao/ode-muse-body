#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
if [ -f server/.env ]; then
    set -a
    . ./server/.env
    set +a
fi
exec "$repo_dir/.venv/bin/python" -m hidagent
