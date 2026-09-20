# 部署教程

目标：在自己的机器或 VPS 上，用 Docker Compose 跑起需要的服务。  
原则：一服务一端口；服务互不知彼此；数据用卷挂载，不打进镜像。

## 0. 前提

- Docker + Docker Compose v2
- 至少能访问 DeepSeek（或兼容 OpenAI Chat Completions 的 API）——问答 / 推荐类服务需要
- **合法持有的** Vic3（AppID `529340`）和/或 HOI4（AppID `394360`）游戏文件；模组按需从工坊订阅后拷贝
- 磁盘：完整语料可达数十 GB；仅 welcome / cards 等轻服务可少很多

克隆后需自行创建并填入下方的 `database/`、`log/` 目录；仓库只提供程序与示例配置。

## 1. 目录约定

在仓库根建立（名称需与 `deploy/docker-compose.yml` 一致）：

```text
database/
  data/steam/
    529340/                    # Vic3
      vanilla/                 # 游戏原文（或削减树）
      mods/<workshop_id>/      # 可选模组
      community/
        workshop_brief/        # *.sqlite（卡片 / 推荐）
        workshop_details/
    394360/                    # HOI4
      vanilla/
      mods/<workshop_id>/
  derived/
    1910/                      # interwar 用
    hoi4/                      # 如 mod_start.json
    1939/                      # 国策 PNG 缓存（可空，运行时生成）
    focus/                     # 国策拓扑衍生
  overview/steam/529340/vanilla/
    localization_assoc.json
    query_aliases.json
    routing_table.json

log/
  1836/ 1837/ 1453/ 1936/ ...  # 按端口分子目录；可先 mkdir 空目录

service/1836/data/mods_catalog.json   # Vic3 模组目录（compose 只读挂载）
service/1936/data/mods_catalog.json   # HOI4（仓库已带一份示例清单，可改）
```

本机开发时，`service/1836/data/game`、`mods` 常为指向 `database/data/steam/...` 的符号链接；Docker 镜像内会去掉链接，改由 compose 卷挂载覆盖。

### 从 examples 拷贝起步

```bash
# Vic3 overview（路由 / 别名 / loc 关联约定）
mkdir -p database/overview/steam/529340/vanilla
cp examples/database/overview/steam/529340/vanilla/*.json \
   database/overview/steam/529340/vanilla/

# 若本机用 symlink（与现网布局一致）
ln -sfn ../../../database/overview/steam/529340/vanilla/localization_assoc.json \
  service/1836/data/localization_assoc.json
ln -sfn ../../../database/overview/steam/529340/vanilla/query_aliases.json \
  service/1836/data/query_aliases.json
ln -sfn ../../../database/overview/steam/529340/vanilla/routing_table.json \
  service/1836/data/routing_table.json

# 模组目录清单
cp examples/mods_catalog.vic3.example.json service/1836/data/mods_catalog.json
# 编辑 mods_catalog.json：填入真实 workshop id / 简称 / 别名

cp examples/mods_catalog.hoi4.example.json service/1936/data/mods_catalog.json
# 或沿用仓库内已有的 service/1936/data/mods_catalog.json
```

`examples/` 里的 overview 是可运行的「数据描述」样例；游戏正文仍须你自己放到 `database/data/.../vanilla`。

## 2. 配置

### 2.1 环境变量（必做）

```bash
cp deploy/.env.example deploy/.env
```

至少填写：

| 变量 | 含义 |
|------|------|
| `DEEPSEEK_API_KEY` | API 密钥（勿提交） |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认 `deepseek-chat` |
| `CONSOLE_PASSWORD` | 运维台密码（务必改掉 example 里的占位） |

`.env.example` 全文即完整模板；`deploy/.env` 已被 git 忽略。

### 2.2 问答请求配置（一般不用改）

容器内使用：

- Vic3：`deploy/patchouli.request.toml` → 拷进镜像为 `patchouli.request.toml`
- HOI4：`deploy/patchouli.1936.request.toml`

本机直接跑 Python 时可参考：

- `service/1836/patchouli/config.example.toml`
- `service/1936/patchouli/config.example.toml`

示例（本机）：

```toml
game_root = "../data/game"
sessions_root = "../sandbox/sessions"
log_dir = "./logs"
env_file = "../llm/.env"
mods_root = "../data/mods"
# mods_catalog = "../data/mods_catalog.json"
max_rounds = 12
submit_grace = 3
max_wall_sec = 180.0
lang = "simp_chinese"
```

本机 LLM 密钥也可放在 `service/1836/llm/.env`（勿提交；格式同 DeepSeek 环境变量）。

### 2.3 `mods_catalog` 字段

见 `examples/mods_catalog.vic3.example.json`：

```json
{
  "schema": "patchouli.mods_catalog.v2",
  "workshop_app": 529340,
  "default_workshop_root": "~/.steam/steam/steamapps/workshop/content/529340",
  "mods": [
    {
      "id": "车间ID",
      "short": "简称",
      "name": "工坊显示名",
      "aliases": ["简称", "别名…"],
      "category": "common"
    }
  ]
}
```

`id` 目录须存在于 `database/data/steam/<appid>/mods/<id>/`（或你挂载的同等路径）。

## 3. 语料从哪来

| 内容 | 说明 |
|------|------|
| `vanilla/` | 从 Steam 安装目录拷贝游戏 `game/` 下脚本与 localization 等；也可用 `ingest/build_reduced_*` 做削减树以减小体积 |
| `mods/<id>/` | 工坊模组目录；可用 `ingest/build_reduced_mod*` 削减 |
| `community/*.sqlite` | 工坊 brief / details；用 `service/1838/scripts/`、`service/1837/scripts/` 等自行抓取生成（**不要**把线上大库提交进 git） |
| `derived/` | 国策图、map editor、mod_start 等；由 `ingest/` 与相关 tool 生成 |

版权：游戏与模组文件仅供个人部署使用，不要公开再分发。

## 4. 启动命令

所有命令在**仓库根**执行。

### 4.1 只起欢迎页（无语料也可）

```bash
docker compose -f deploy/docker-compose.yml build welcome
docker compose -f deploy/docker-compose.yml up -d welcome
# http://<主机>:80
```

### 4.2 Vic3 问答（1836）

准备好：

- `database/data/steam/529340/vanilla`
- （可选）`mods/` + `service/1836/data/mods_catalog.json`
- `deploy/.env` 中的 API Key

```bash
mkdir -p log/1836
docker compose -f deploy/docker-compose.yml build web
docker compose -f deploy/docker-compose.yml up -d web
# http://<主机>:1836
```

只重建问答、不动其它服务：

```bash
docker compose -f deploy/docker-compose.yml up -d --no-deps --build web
```

### 4.3 模组推荐 / 卡片（1837 / 1838）

需挂载的 SQLite（路径见 compose）：

- `database/data/steam/529340/community/workshop_details/workshop_details_all.sqlite`
- 同目录 `workshop_details_ge300.sqlite`
- `.../workshop_brief/vic3_mods_all.sqlite`、`vic3_mods_ge300.sqlite`

```bash
mkdir -p log/1837
docker compose -f deploy/docker-compose.yml build recommend cards
docker compose -f deploy/docker-compose.yml up -d recommend cards
# :1837 推荐  ·  :1838 卡片
```

### 4.4 运维控制台（1453）

只读 `log/1836`、`log/1837`；改好 `CONSOLE_PASSWORD`：

```bash
mkdir -p log/1453
docker compose -f deploy/docker-compose.yml build console
docker compose -f deploy/docker-compose.yml up -d console
# http://<主机>:1453
```

### 4.5 Interwar / HOI4 / 国策图

```bash
# Interwar：准备 database/derived/1910
docker compose -f deploy/docker-compose.yml build interwar && \
  docker compose -f deploy/docker-compose.yml up -d interwar

# HOI4 问答：准备 394360 vanilla/mods、mods_catalog、log/1936
docker compose -f deploy/docker-compose.yml build hoi4 && \
  docker compose -f deploy/docker-compose.yml up -d hoi4

# 国策总览+出图：见 compose 中 focus1939 卷
docker compose -f deploy/docker-compose.yml build focus1939 && \
  docker compose -f deploy/docker-compose.yml up -d focus1939
```

### 4.6 常用运维

```bash
# 看状态
docker compose -f deploy/docker-compose.yml ps

# 只看某服务日志
docker compose -f deploy/docker-compose.yml logs -f --tail=100 web

# 停某服务
docker compose -f deploy/docker-compose.yml stop web
```

**禁止**为了改 A 而去 recreate 全体；不要加 `depends_on`。

## 5. 本机不用 Docker（可选）

适合开发单个服务。以 Vic3 web 为例：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r service/1836/web/requirements.txt

export PYTHONPATH="$PWD/service/1836:$PWD"
export PATCHOULI_CONFIG="$PWD/service/1836/patchouli/config.example.toml"
# 按 example 改路径；或 export DEEPSEEK_* 

cd service/1836
python3 -m web
```

确保 `data/game`、`data/mods` 指向真实语料，且 overview 三个 JSON 可解析（文件或 symlink）。

## 6. 安全组 / 防火墙

按你实际对外暴露的端口放行，例如：`80`、`1836`、`1837`、`1838`、`1910`、`1936`、`1939`。  
`1453` 建议仅内网或 VPN；不要挂到欢迎页。

## 7. 启动前核对

- [ ] 已复制 `deploy/.env.example` → `deploy/.env`，并填写 `DEEPSEEK_API_KEY`
- [ ] 已将 `CONSOLE_PASSWORD` 改成自己的口令
- [ ] 所需服务对应的 `database/` 路径已按第 1 节放好
- [ ] `mods_catalog.json`（若用模组）已按 `examples/` 改好 workshop id
