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

## 仓库结构

```
service/<端口>/   各独立服务
tool/             领域工具
common/           共用 Agent 壳
ingest/           语料加工脚本
deploy/           Docker Compose 与环境变量示例
examples/         配置与数据布局示例
docs/             部署教程
```

部署时还需在本地准备游戏语料目录（见部署文档中的 `database/` 约定）并填写 API 密钥。

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

## 许可证

源码以 [MIT License](LICENSE) 发布。

**游戏本体与模组文件受 Paradox / 模组作者版权保护，不随本仓库分发。** 部署者须自备合法游戏副本，并自行准备工坊元数据（见部署文档）。
