#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/runtime/meta" "$ROOT/work/hackathon/meta-models"
if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  echo 'An Ollama server is already available on loopback port 11434. No service or device profile was changed.'
  exit 0
fi
OLLAMA_BIN="$(command -v ollama || true)"
[ -n "$OLLAMA_BIN" ] || { echo 'Install Ollama from its official distribution first.'; exit 1; }
nohup env OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$ROOT/work/hackathon/meta-models" \
  OLLAMA_CONTEXT_LENGTH=4096 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 \
  OLLAMA_KEEP_ALIVE=60s OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  "$OLLAMA_BIN" serve > "$ROOT/runtime/meta/server.log" 2>&1 < /dev/null &
echo $! > "$ROOT/runtime/meta/server.pid"
echo 'Started loopback-only local inference. No model download or device-engine switch was requested.'
wait
