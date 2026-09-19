# 预解析国策 derived

## 产物位置

`database/derived/focus/<mod_short>/`

| 文件 | 内容 |
|------|------|
| `manifest.json` | 构建时间、源指纹、统计 |
| `catalog.json` | 专属 TAG / 非专属树总览 + `summary_text` |
| `index.json` | `tree_id` → 文件名与分类元数据 |
| `trees/<id>.json` | 完整图：nodes（含 loc 名）、prereq_edges、mex_edges |

注册表：`database/derived/focus/registry.json`  
按工坊 id：`database/derived/focus/by_id/<id>/`（指向对应 short 目录）

## 构建（一次一个模组）

在仓库根目录：

```bash
python ingest/focus_corpus/build_focus_corpus.py --mod TFR
```

不要默认全量；需要哪个模组再显式 `--mod`。

## Agent / 找侧

找侧工具优先读 derived（有则不再现扫 `national_focus`）：

- `focus_tree_catalog` → `catalog.json`
- `focus_topology` → 仍可对 live 语料建拓扑；查单树结构也可用 `focus_derived` 读 `trees/*.json`
- `focus_derived` → 列已构建模组、读 catalog / 单树 graph

Cursor 规则：`.cursor/rules/hoi4-focus-derived.mdc`
