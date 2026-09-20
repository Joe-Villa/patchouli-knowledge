# examples/

给开源克隆者用的**可复制模板**，不是运行时必挂路径。

| 路径 | 用途 |
|------|------|
| `mods_catalog.vic3.example.json` | 复制为 `service/1836/data/mods_catalog.json` 后改 workshop id |
| `mods_catalog.hoi4.example.json` | 复制为 `service/1936/data/mods_catalog.json`（或沿用仓库已有清单） |
| `database/overview/steam/529340/vanilla/*.json` | Vic3 数据描述：loc 关联约定、查询别名、薄路由表 |

完整部署步骤见 [docs/DEPLOY.md](../docs/DEPLOY.md)。

不要把真实 `database/data`（游戏原文）、`*.sqlite`、`log/` 放进本目录再提交。
