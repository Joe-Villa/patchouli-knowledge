# Patchouli Knowledge（帕秋莉的知识）

基于 **本地游戏语料 + 领域工具 + Agent**，回答通用大模型答不好、答不快的 Paradox 游戏问题；同时提供模组浏览、推荐、国策图等「不用 Agent 也能帮到人」的能力。

核心理念：把分散在社区、更新日志、本地文件里的知识 **集中 → 规范成数据 → 再应用**。数据在本地，才能做预处理、做确定性衍生，并明确能力边界。

## 能做什么

| 服务 | 端口 | 说明 |
|------|------|------|
| welcome | 80 | 欢迎页（用户入口目录） |
| web | 1836 | Victoria 3 机制问答 |
| recommend | 1837 | Vic3 工坊模组推荐（Agent） |
| cards | 1838 | Vic3 工坊模组卡片浏览 / 搜索 |
| console | 1453 | 运维控制台（只读日志；不对欢迎页展示） |
| interwar | 1910 | Interwar 相关站点 |
| hoi4 | 1936 | Hearts of Iron IV 问答 |
| focus1939 | 1939 | HOI4 国策树总览与出图 |

各服务 **相互独立**：可共享数据卷，但无进程依赖、无共用镜像、无 `depends_on`。改 A 只重建 / 重启 A。

## 仓库结构（精简）

```
service/<端口>/   应用层：各独立服务
tool/             数据应用层：领域工具（读 database，不被 data 反依赖）
common/           共用 Agent 壳（找侧 loop / evidence / llm）
ingest/           语料加工脚本（衍生数据由程序生成）
deploy/           Docker Compose、Dockerfile、环境变量示例
examples/         开源用配置 / 数据布局示例（可复制后改）
docs/             部署与数据准备教程
database/         本地语料（不进仓库；自行准备）
log/              运行日志（不进仓库）
file/             私人笔记（不进仓库）
```

## 快速开始

完整步骤（数据目录、配置 example、启动命令）见：

→ **[docs/DEPLOY.md](docs/DEPLOY.md)**

最短路径（已准备好语料与 `.env` 时）：

```bash
cp deploy/.env.example deploy/.env   # 填写 DEEPSEEK_API_KEY 等
# 按 docs/DEPLOY.md 放好 database/ 与 mods_catalog
docker compose -f deploy/docker-compose.yml build welcome web
docker compose -f deploy/docker-compose.yml up -d welcome web
```

## 不会上传什么

仓库刻意不包含：

- `database/` — 游戏原文 / 模组 / SQLite 等大语料（含版权内容，需自备）
- `log/` — 运行日志与会话痕迹
- `file/`、`TODO/` — 私人设计笔记
- `.env`、真实 `config.toml`、密钥

请只提交源码、示例配置与文档。详见 `.gitignore`。

## 许可证与游戏资源

本仓库源码的许可证待定（开源发布前请自行选定并补 `LICENSE`）。

**游戏本体与模组文件受 Paradox / 模组作者版权保护，不得随本仓库分发。** 部署者须自备合法游戏副本，并自行准备工坊元数据（见部署文档）。
