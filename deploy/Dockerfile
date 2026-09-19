# Patchouli Vic3 问答 web=:1836（不含 archive / QQ 管线）
# 构建：docker compose -f deploy/docker-compose.yml build web
# 语料与 log 通过卷挂载

FROM python:3.12-slim-bookworm

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g; s|security.debian.org|mirrors.cloud.tencent.com|g' \
        /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
      sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g; s|security.debian.org|mirrors.cloud.tencent.com|g' \
        /etc/apt/sources.list; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        bubblewrap \
        ca-certificates \
        ripgrep \
        tini \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY service/1836/web/requirements.txt /tmp/web-requirements.txt
RUN pip install --no-cache-dir \
      -i https://mirrors.cloud.tencent.com/pypi/simple \
      --trusted-host mirrors.cloud.tencent.com \
      -r /tmp/web-requirements.txt \
    && rm /tmp/web-requirements.txt

COPY service/1836 /app/service/1836
COPY tool /app/tool
COPY common /app/common
# 运行时 shim（localization_db / common_registry）依赖的最小 ingest 子集
COPY ingest/_pathsetup.py /app/ingest/_pathsetup.py
COPY ingest/_lib /app/ingest/_lib
COPY ingest/common_registry /app/ingest/common_registry
COPY deploy/patchouli.request.toml /app/service/1836/patchouli.request.toml
COPY deploy/entrypoint.web.sh /entrypoint.sh

# 本机 data/game|mods 常为指向 database 的 symlink；镜像内去掉后建空目录，由卷挂载覆盖
RUN chmod +x /entrypoint.sh \
    && rm -rf /app/service/1836/data/game /app/service/1836/data/mods \
    && mkdir -p \
        /app/service/1836/data/game \
        /app/service/1836/data/mods \
        /app/service/1836/sandbox/sessions \
        /app/log/1836/web

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/service/1836:/app \
    CORE_ROOT=/app/service/1836 \
    PATCHOULI_CONFIG=/app/service/1836/patchouli.request.toml \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=1836

WORKDIR /app/service/1836

EXPOSE 1836

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["web"]
