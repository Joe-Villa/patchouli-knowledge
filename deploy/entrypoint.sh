#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
  /app/核心模块/sandbox/sessions \
  /app/核心模块/patchouli/logs \
  /app/和QQ的通信管线/logs/framework \
  /app/和QQ的通信管线/runtime

if [[ ! -f /app/和QQ的通信管线/.env ]]; then
  # 允许仅用 docker env / --env-file，不必落盘 .env
  touch /app/和QQ的通信管线/.env
fi

mode="${1:-web}"
shift || true

case "$mode" in
  web)
    exec python3 -m gatekeeper.web "$@"
    ;;
  bot|qq)
    # 本机 NapCat/OneBot；服务器不启
    exec python3 -m bot "$@"
    ;;
  qqbot|official-qq)
    # 开放平台官方机器人（独立服务）
    exec python3 -m qqbot "$@"
    ;;
  heartbeat)
    exec python3 -m heartbeat "$@"
    ;;
  console)
    exec python3 -m console "$@"
    ;;
  patchouli)
    # 无管线：直接跑核心 CLI。例：patchouli -q '问题'
    cd /app/核心模块
    exec python3 -m patchouli "$@"
    ;;
  bwrap-check)
    # 验收容器内嵌套命名空间是否可用（python 在 slim 镜像里多在 /usr/local）
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
