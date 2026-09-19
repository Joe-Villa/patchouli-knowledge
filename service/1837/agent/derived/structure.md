# 工坊 details 表（与 1838 共用 workshop_details_all.sqlite）

主表：`details`

| 字段 | 含义 |
|------|------|
| id | Steam publishedfileid（Mod ID） |
| title | API 原始标题快照 |
| title_zh | 中文模式标题 |
| title_en | 英文模式标题 |
| description | 工坊简介（BBCode） |
| author | 作者显示名 |
| creator | SteamID64 |
| subscriptions | 当前订阅 |
| favorited / views | 收藏 / 浏览 |
| tags | 标签，` \| ` 分隔 |
| file_size | 字节（文本数字） |
| time_created / time_updated | unix 时间戳 |
| created / updated | 日期字符串 |
| url / preview_url | 链接 |

环境变量 `WORKSHOP_DB` 指向 sqlite 路径；沙箱内也可用 `/data/workshop.sqlite`（由服务挂载）。

## 推荐查法

1. **统计 / 排名 / 作者计数**：`run_code` + sqlite3（`SELECT … FROM details`）
2. **尖需求（专名、精确短语）**：`grep_text` 搜 title_zh/title_en/description，或 SQL `LIKE`
3. **糊需求、候选面大**：`recommend_mods`（Fit 打分召回），不要用它做「谁最多」类题
4. 提到模组时若 title_zh ≠ title_en，证据与回答都要带上两边
