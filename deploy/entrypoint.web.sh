#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
  /app/service/1836/sandbox/sessions \
  /app/log/1836/web

mode="${1:-web}"
shift || true

case "$mode" in
  web)
    cd /app/service/1836
    export PYTHONPATH="/app/service/1836:/app${PYTHONPATH:+:$PYTHONPATH}"
    exec python3 -m web "$@"
    ;;
  patchouli)
    cd /app/service/1836
    export PYTHONPATH="/app/service/1836:/app${PYTHONPATH:+:$PYTHONPATH}"
    exec python3 -m patchouli "$@"
    ;;
  bwrap-check)
    py="$(command -v python3)"
    exec bwrap --ro-bind /usr /usr --ro-bind /lib /lib \
      --ro-bind /usr/local /usr/local \
      --symlink usr/lib64 /lib64 --ro-bind /bin /bin \
      --dev /dev --proc /proc --tmpfs /tmp \
      --unshare-net --unshare-pid --die-with-parent --new-session \
      --clearenv --setenv PATH /usr/local/bin:/usr/bin:/bin \
      "$py" -c 'import socket
try:
  socket.create_connection(("1.1.1.1", 80), 2)
  print("NET_OPEN")
except OSError as e:
  print("NET_BLOCKED", type(e).__name__)
print("bwrap_ok")'
    ;;
  *)
    exec "$mode" "$@"
    ;;
esac
