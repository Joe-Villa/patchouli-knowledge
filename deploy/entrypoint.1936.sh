#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
  /app/service/1936/sandbox/sessions \
  /app/log/1936/web

mode="${1:-web}"
shift || true

case "$mode" in
  web)
    cd /app/service/1936
    export PYTHONPATH="/app/service/1936:/app${PYTHONPATH:+:$PYTHONPATH}"
    exec python3 -m web "$@"
    ;;
  patchouli)
    cd /app/service/1936
    export PYTHONPATH="/app/service/1936:/app${PYTHONPATH:+:$PYTHONPATH}"
    exec python3 -m patchouli "$@"
    ;;
  *)
    exec "$mode" "$@"
    ;;
esac
