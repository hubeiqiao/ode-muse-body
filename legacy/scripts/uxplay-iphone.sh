#!/bin/sh
# Archived optional separate receiver; operator chooses its identity.
if [ -n "${UXPLAY_MAC:-}" ]; then set -- "$@" -m "$UXPLAY_MAC"; fi
exec "${UXPLAY_BIN:-uxplay}" "$@" -vsync no -vd "avdec_h264 max-threads=1 thread-type=slice"
