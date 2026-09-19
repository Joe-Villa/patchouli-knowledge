"""找侧工具实现 + OpenAI/DeepSeek function schemas。"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from localization_bridge import (
    connect as loc_connect,
    forward_bridge,
    lookup_exact_expanded,
    reverse_exact,
    reverse_fuzzy,
)
from read_block import read_block as _read_block

# sandbox imported lazily / injected


_REPO = Path(__file__).resolve().parents[2]
DEFAULT_DATA = _REPO / "service" / "1836" / "data"
DEFAULT_GAME = DEFAULT_DATA / "game"
DEFAULT_ROUTING = DEFAULT_GAME / "routing_table.json"
DEFAULT_ALIASES = DEFAULT_DATA / "query_aliases.json"

_TEXT_PREVIEW = 3500
_GREP_MAX_HITS = 40
_GREP_LINE_MAX = 240
# ripgrep 风格：按行流式扫，限制累计读盘（避免整文件 read_text 拖死小内存机）
_GREP_MAX_FILES = 500
_GREP_MAX_BYTES = 12 * 1024 * 1024  # 单次工具调用累计可读 ~12MiB
_GREP_MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件最多读前 2MiB
_GREP_RG_TIMEOUT_SEC = 20.0


def _tool(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _tool(
        "route_query",
        "根据问题关键词匹配 routing_table，返回优先查询资产。找的第一步可用。",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户原问题或摘要"},
            },
            "required": ["question"],
        },
    ),
    _tool(
        "loc_search",
        "人话/中英文案 → localization keys。先 exact 再 fuzzy(FTS)。",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "lang": {"type": "string", "default": "simp_chinese"},
                "limit": {"type": "integer", "default": 15},
            },
            "required": ["text"],
        },
    ),
    _tool(
        "loc_lookup",
        "按 loc key 精确查文案（可展开 $REF$）；或按实体 kind+key 做正向桥。",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "localization key 或实体 key"},
                "mode": {
                    "type": "string",
                    "enum": ["exact", "forward"],
                    "default": "exact",
                },
                "entity_kind": {
                    "type": "string",
                    "description": "mode=forward 时必填，如 country/ideology/law/event",
                },
                "lang": {"type": "string", "default": "simp_chinese"},
                "expand": {"type": "boolean", "default": True},
            },
            "required": ["key"],
        },
    ),
    _tool(
        "loc_to_entity",
        "loc_key → 实体（loc_entity_index 第二跳）。",
        {
            "type": "object",
            "properties": {
                "loc_key": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["loc_key"],
        },
    ),
    _tool(
        "registry_lookup",
        "脚本 key → 所在文件。kind=common|events|map|hub。"
        "mode=exact 精确；prefix/contains 在 key 上模糊（找 lawgroup_migration 等）。",
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["common", "events", "map", "hub"],
                },
                "key": {"type": "string"},
                "type": {
                    "type": "string",
                    "description": "common 时可按子目录 type 过滤，如 ideologies / law_groups / laws",
                },
                "mode": {
                    "type": "string",
                    "enum": ["exact", "prefix", "contains"],
                    "default": "exact",
                },
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["kind", "key"],
        },
    ),
    _tool(
        "focus_topology",
        "HOI4：按 TAG 建出国策逻辑拓扑（点=国策，边=prerequisite，互斥叉=路线分支）。"
        "HOI4 剧情/路线几乎都在国策树上：问内战、开战、变线、走哪条线时必须先用本工具（或 focus_derived），"
        "再按需 read_block；不要先盲搜 events。"
        "返回 summary_text（可直接作 computation 证据）以及每棵树的 roots/forks。",
        {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "三字母国家 tag，如 PRC / GER / SIA",
                },
                "tree_id": {
                    "type": "string",
                    "description": "可选：只看某一 focus_tree id，如 china_nationalist_focus",
                },
                "outline_depth": {
                    "type": "integer",
                    "default": 2,
                    "description": "入口向下展开层数（默认 2）",
                },
            },
            "required": ["tag"],
        },
    ),
    _tool(
        "focus_tree_catalog",
        "HOI4：扫描当前语料全部 focus_tree，二分「专属（恰好一 TAG）」与「非专属」。"
        "剧情入口总览：先弄清有哪些树再下钻。若已构建 database/derived/focus/<mod>/ 则优先读 derived（快）。"
        "summary_text 可直接作 computation 证据。细看某 TAG 用 focus_topology；单树全图用 focus_derived。",
        {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "可选过滤：专属 / 非专属（或 exclusive / non_exclusive）",
                },
                "tag": {
                    "type": "string",
                    "description": "可选：只返回专属该 TAG 的树",
                },
                "live": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 则强制现扫 national_focus，忽略 derived",
                },
            },
            "required": [],
        },
    ),
    _tool(
        "focus_derived",
        "HOI4：读取预解析国策 derived（database/derived/focus）。剧情答题的主数据源之一。"
        "action=list 看哪些模组已构建；catalog 读专属/非专属总览；"
        "index 列树；tree 读单棵树完整 graph（节点名、prereq/mex、坐标）。"
        "未构建时返回 hint：python ingest/focus_corpus/build_focus_corpus.py --mod <short>。"
        "不要擅自全量解析所有模组。",
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "catalog", "index", "tree"],
                    "description": "list | catalog | index | tree",
                },
                "tree_id": {
                    "type": "string",
                    "description": "action=tree 时必填：focus_tree id",
                },
                "tag": {
                    "type": "string",
                    "description": "action=index 时可选：只列该 TAG 专属树",
                },
                "mod": {
                    "type": "string",
                    "description": "可选：模组 short/id；默认用当前语料 game_root 解析",
                },
            },
            "required": ["action"],
        },
    ),
    _tool(
        "read_block",
        "在已知 path 中按 key 切 Clausewitz 块，产出答用 span。depth=any 用于嵌套如 named_colors。"
        "path 相对 /game；整池模式也可用 mods/<workshop_id>/...。",
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对 game/，或 mods/<id>/common/...",
                },
                "key": {"type": "string"},
                "depth": {"type": "string", "enum": ["top", "any"], "default": "top"},
                "include_text": {
                    "type": "boolean",
                    "default": True,
                    "description": "false 时只返回行号（预览）",
                },
            },
            "required": ["path", "key"],
        },
    ),
    _tool(
        "grep_text",
        "在文本树中按行搜索（ripgrep 风格，只读）。必须带目录前缀的 glob；"
        "禁止裸 **/*.txt。命中或读盘预算用尽即停。整池：mods/<id>/common/**/*.txt。",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {
                    "type": "string",
                    "description": (
                        "必填倾向：带顶层目录，如 common/national_focus/*.txt、"
                        "events/TNO_Burgundy*.txt、mods/<id>/common/decisions/**/*.txt。"
                        "不要用 **/*.txt 或省略 glob。"
                    ),
                },
                "max_hits": {"type": "integer", "default": 30},
                "literal": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 则按字面量子串，不作正则",
                },
            },
            "required": ["pattern"],
        },
    ),
    _tool(
        "read_lines",
        "按行号读取文本文件片段（只读）。path 相对 game/ 或 mods/<id>/...。",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对 game/ 或 mods/<id>/..."},
                "start_line": {"type": "integer", "description": "1-based 起始行"},
                "end_line": {"type": "integer", "description": "1-based 结束行（含）"},
            },
            "required": ["path", "start_line", "end_line"],
        },
    ),
    _tool(
        "run_code",
        "在沙箱中执行 Python。game 只读挂载为 /game（也可用环境变量 PATCHOULI_GAME）。"
        "用于颜色转换、批量统计/排名/求和、跨文件聚合、临时解析等。"
        "用 print 输出结果。禁止联网。"
        "统计类问题算出答案后，必须把 stdout 写入 submit 的 items[].text"
        "（entity_type=computation, role=primary），coverage=sufficient；不要只把结果写在 notes。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout_sec": {"type": "number", "default": 8},
            },
            "required": ["code"],
        },
    ),
    _tool(
        "submit_evidence_package",
        "结束查找并提交答用证据包。两类最终证据："
        "（1）脚本块：path+key，系统再读全文（可省略 text）；"
        "（2）计算结果：entity_type=computation + text=run_code 的 stdout（或等价汇总），"
        "path 可用 run_code 或数据目录，key 可用 computation_result；role=primary。"
        "不要塞检索中间跳转块。若歧义/找不到也要提交，设好 coverage 与 unresolved。"
        "重要：arguments 必须是合法 JSON；why/notes/label 中禁止未转义的双引号（可用「」或省略 why）。"
        "脚本块优先只交 path+key+role；计算结果必须交 text，否则答侧无法使用。",
        {
            "type": "object",
            "properties": {
                "coverage": {
                    "type": "string",
                    "enum": ["sufficient", "partial", "empty", "ambiguous"],
                },
                "question_focus": {"type": "string"},
                "resolved_entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "key": {"type": "string"},
                            "label": {"type": "string"},
                            "entity_type": {"type": "string"},
                        },
                        "required": ["query", "key"],
                    },
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "key": {"type": "string"},
                            "role": {
                                "type": "string",
                                "enum": ["primary", "definition", "support"],
                            },
                            "entity_type": {
                                "type": "string",
                                "description": "computation=沙箱/批量统计结果；其它为脚本实体类型",
                            },
                            "label": {"type": "string"},
                            "why": {"type": "string"},
                            "depth": {
                                "type": "string",
                                "enum": ["top", "any"],
                                "default": "top",
                            },
                            "text": {
                                "type": "string",
                                "description": (
                                    "脚本块一般省略（由 path+key 填充）；"
                                    "computation 必须填写 run_code 输出或汇总表"
                                ),
                            },
                        },
                    },
                },
                "unresolved": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string"},
                            "key": {"type": "string"},
                            "query": {"type": "string"},
                        },
                        "required": ["reason"],
                    },
                },
                "not_expanded": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string"},
                            "key": {"type": "string"},
                            "query": {"type": "string"},
                        },
                        "required": ["reason"],
                    },
                },
                "notes": {"type": "string"},
            },
            "required": ["coverage", "items"],
        },
    ),
]


# grep/read 默认可扫顶层（Vic3）；HOI4 等可在 ToolContext.scan_tops 覆盖
DEFAULT_SCAN_TOPS: tuple[str, ...] = ("common", "events", "map_data")


@dataclass
class ToolContext:
    game_root: Path
    loc_db: Path
    sandbox: Any  # SandboxSession | None
    lang: str = "simp_chinese"
    # pool：host 上 _mods_view（id→削减树）
    mods_root: Path | None = None
    visible_mod_ids: frozenset[str] = field(default_factory=frozenset)
    scan_tops: tuple[str, ...] = DEFAULT_SCAN_TOPS


def _normalize_rel_path(path: str) -> str:
    p = (path or "").strip().lstrip("/")
    if p.startswith("game/"):
        p = p[5:]
    return p


def _resolve_corpus_path(
    ctx: ToolContext, path: str
) -> dict[str, Any] | tuple[Path, Path, str]:
    """解析相对路径 → (abs_path, root, rel_under_root)。

    支持：
    - 相对 /game（如 common/...）
    - mods/<workshop_id>/...（仅 pool 可见模组）
    """
    p = _normalize_rel_path(path)
    if not p:
        return {"ok": False, "error": "empty_path"}

    if p == "mods" or p.startswith("mods/"):
        if ctx.mods_root is None:
            return {"ok": False, "error": "mods_not_mounted"}
        rest = "" if p == "mods" else p[5:]
        if not rest:
            return ctx.mods_root.resolve(), ctx.mods_root.resolve(), ""
        parts = rest.split("/", 1)
        mid = parts[0].strip()
        if mid not in ctx.visible_mod_ids:
            return {"ok": False, "error": "mod_not_in_pool", "mod_id": mid}
        mod_root = (ctx.mods_root / mid).resolve()
        if not mod_root.is_dir():
            return {"ok": False, "error": "mod_dir_missing", "mod_id": mid}
        if len(parts) == 1 or not parts[1]:
            return mod_root, mod_root, ""
        rel = parts[1]
        fp = (mod_root / rel).resolve()
        try:
            fp.relative_to(mod_root)
        except ValueError:
            return {"ok": False, "error": "path_outside_mod"}
        return fp, mod_root, rel

    root = ctx.game_root.resolve()
    fp = (root / p).resolve()
    try:
        fp.relative_to(root)
    except ValueError:
        return {"ok": False, "error": "path_outside_game"}
    return fp, root, p


def make_dispatcher(ctx: ToolContext) -> dict[str, Callable[[dict], Any]]:
    return {
        "route_query": lambda a: tool_route_query(a, ctx),
        "loc_search": lambda a: tool_loc_search(a, ctx),
        "loc_lookup": lambda a: tool_loc_lookup(a, ctx),
        "loc_to_entity": lambda a: tool_loc_to_entity(a, ctx),
        "registry_lookup": lambda a: tool_registry_lookup(a, ctx),
        "focus_topology": lambda a: tool_focus_topology(a, ctx),
        "focus_tree_catalog": lambda a: tool_focus_tree_catalog(a, ctx),
        "focus_derived": lambda a: tool_focus_derived(a, ctx),
        "read_block": lambda a: tool_read_block(a, ctx),
        "read_lines": lambda a: tool_read_lines(a, ctx),
        "grep_text": lambda a: tool_grep_text(a, ctx),
        "run_code": lambda a: tool_run_code(a, ctx),
        # submit 由 agent 环特殊处理
    }


def _load_query_aliases() -> list[dict]:
    for cand in (
        DEFAULT_GAME / "query_aliases.json",
        DEFAULT_ALIASES,
        DEFAULT_DATA / "query_aliases.json",
    ):
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                return list(data.get("aliases") or [])
            except (OSError, json.JSONDecodeError):
                return []
    return []


def _alias_hits_for_question(question: str) -> list[dict]:
    """人话命中 query_aliases 时返回建议实体/路径。"""
    q = question or ""
    if not q:
        return []
    hits: list[dict] = []
    for entry in _load_query_aliases():
        phrases = entry.get("phrases") or []
        matched = [p for p in phrases if p and p in q]
        if not matched:
            continue
        hits.append(
            {
                "matched_phrases": matched,
                "suggest": entry.get("suggest") or [],
                "prefer_paths": entry.get("prefer_paths") or [],
            }
        )
    return hits


def tool_route_query(args: dict, ctx: ToolContext) -> dict:
    q = str(args.get("question") or "")
    path = ctx.game_root / "routing_table.json"
    if not path.is_file():
        path = DEFAULT_ROUTING
    data = json.loads(path.read_text(encoding="utf-8"))
    routes = data.get("routes") or []
    matched = []
    ql = q.lower()
    for r in routes:
        if r.get("id") == "fallback":
            continue
        kws = r.get("keywords") or []
        hits = [k for k in kws if k.lower() in ql or k in q]
        if hits:
            item = {
                "id": r.get("id"),
                "prefer": r.get("prefer"),
                "hit_keywords": hits,
            }
            if r.get("hints"):
                item["hints"] = r.get("hints")
            matched.append(item)
    if not matched:
        fb = next((r for r in routes if r.get("id") == "fallback"), None)
        if fb:
            matched.append(
                {"id": "fallback", "prefer": fb.get("prefer"), "hit_keywords": []}
            )
    out: dict[str, Any] = {
        "ok": True,
        "matched": matched,
        "notes": data.get("notes"),
        "assets": data.get("assets"),
    }
    aliases = _alias_hits_for_question(q)
    if aliases:
        out["query_aliases"] = aliases
        out["alias_action"] = (
            "已命中人话别名：优先按 suggest/prefer_paths 打开文件，勿被模糊 loc 带偏。"
        )
    return out


def tool_loc_search(args: dict, ctx: ToolContext) -> dict:
    text = str(args.get("text") or "").strip()
    lang = str(args.get("lang") or ctx.lang)
    limit = int(args.get("limit") or 15)
    if not text:
        return {"ok": False, "error": "empty_text"}
    conn = loc_connect(ctx.loc_db)
    try:
        exact = reverse_exact(conn, text, lang)
        fuzzy = reverse_fuzzy(conn, text, lang, limit=limit)
        exact_rows = [_loc_row(r) for r in exact[:limit]]
        fuzzy_rows = [_loc_row(r) for r in fuzzy[:limit]]
        country_cands = _country_name_candidates(ctx, exact_rows + fuzzy_rows)
        out: dict[str, Any] = {
            "ok": True,
            "exact": exact_rows,
            "fuzzy": fuzzy_rows,
        }
        if country_cands:
            out["country_tag_candidates"] = country_cands
            if len(country_cands) > 1:
                out["ambiguity_hint"] = (
                    "多个国家 tag 共享该显示名；若问题未指明，应 coverage=ambiguous 并列出候选，"
                    "不要擅自选一个作答。"
                )
        aliases = _alias_hits_for_question(text)
        if aliases:
            out["query_aliases"] = aliases
            out["alias_hint"] = (
                "下列别名优先于模糊 loc 命中：按 suggest.key 去 registry/read_block，"
                "不要把无关 gatekeeping/义务 词条当主实体。"
            )
        return out
    finally:
        conn.close()


def tool_loc_lookup(args: dict, ctx: ToolContext) -> dict:
    key = str(args.get("key") or "").strip()
    mode = str(args.get("mode") or "exact")
    lang = str(args.get("lang") or ctx.lang)
    expand = bool(args.get("expand", True))
    if not key:
        return {"ok": False, "error": "empty_key"}
    conn = loc_connect(ctx.loc_db)
    try:
        if mode == "forward":
            kind = str(args.get("entity_kind") or "").strip()
            if not kind:
                return {"ok": False, "error": "forward_needs_entity_kind"}
            hits = forward_bridge(conn, kind, key, lang, expand=expand)
            return {"ok": True, "hits": [_loc_row(r) for r in hits]}
        row = lookup_exact_expanded(conn, key, lang) if expand else None
        if expand and row:
            return {"ok": True, "hit": _loc_row(row)}
        # non-expand path
        from localization_bridge import lookup_exact

        row2 = lookup_exact(conn, key, lang)
        if not row2:
            return {"ok": False, "error": "not_found", "key": key}
        return {"ok": True, "hit": _loc_row(row2)}
    finally:
        conn.close()


def tool_loc_to_entity(args: dict, ctx: ToolContext) -> dict:
    loc_key = str(args.get("loc_key") or "").strip()
    limit = int(args.get("limit") or 20)
    if not loc_key:
        return {"ok": False, "error": "empty_loc_key"}
    db = ctx.game_root / "loc_entity_index.sqlite"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT loc_key, entity_kind, entity_key, relation
            FROM loc_entity WHERE loc_key=? LIMIT ?
            """,
            (loc_key, limit),
        ).fetchall()
        return {"ok": True, "hits": [dict(r) for r in rows]}
    finally:
        conn.close()


def tool_focus_tree_catalog(args: dict, ctx: ToolContext) -> dict:
    """扫描语料全部国策树，二分专属 / 非专属；有 derived 则优先读。"""
    import sys

    tool_root = Path(__file__).resolve().parents[1]
    if str(tool_root) not in sys.path:
        sys.path.insert(0, str(tool_root))
    from focus_topology import catalog_focus_trees  # noqa: WPS433
    from focus_topology.derived import (  # noqa: WPS433
        load_catalog_json,
        resolve_derived_dir,
    )

    category = args.get("category")
    tag = args.get("tag")
    live = bool(args.get("live"))

    if not live:
        derived = resolve_derived_dir(game_root=ctx.game_root)
        cached = load_catalog_json(derived) if derived else None
        if cached and cached.get("ok"):
            return _filter_derived_catalog(
                cached, category=category, tag=tag, derived=str(derived)
            )

    result = catalog_focus_trees(
        ctx.game_root,
        category=str(category).strip() if category else None,
        tag=str(tag).strip() if tag else None,
    )
    if result.get("ok"):
        result = dict(result)
        result["source"] = "live"
    return result


def _filter_derived_catalog(
    cached: dict,
    *,
    category: object,
    tag: object,
    derived: str,
) -> dict:
    cat_filter = str(category or "").strip().lower()
    tag_f = str(tag or "").strip()
    exclusive_by_tag = dict(cached.get("exclusive_by_tag") or {})
    non_exclusive = list(cached.get("non_exclusive") or [])

    rows: list[dict] = []
    for tag_k, trees in exclusive_by_tag.items():
        for t in trees:
            row = dict(t)
            row.setdefault("category", "专属")
            row.setdefault("tag", tag_k)
            rows.append(row)
    for t in non_exclusive:
        row = dict(t)
        row.setdefault("category", "非专属")
        rows.append(row)

    if cat_filter in ("专属", "exclusive", "excl"):
        rows = [r for r in rows if r.get("category") == "专属"]
    elif cat_filter in ("非专属", "non_exclusive", "non-exclusive", "nonexcl"):
        rows = [r for r in rows if r.get("category") == "非专属"]

    if tag_f:
        tu = tag_f.upper()
        rows = [
            r
            for r in rows
            if r.get("category") == "专属" and str(r.get("tag") or "").upper() == tu
        ]
        exclusive_by_tag = {
            k: v for k, v in exclusive_by_tag.items() if str(k).upper() == tu
        }
        non_exclusive = []
    elif cat_filter in ("专属", "exclusive", "excl"):
        non_exclusive = []
    elif cat_filter in ("非专属", "non_exclusive", "non-exclusive", "nonexcl"):
        exclusive_by_tag = {}

    exclusive = [r for r in rows if r.get("category") == "专属"]
    if cat_filter in ("非专属", "non_exclusive", "non-exclusive", "nonexcl"):
        exclusive = []
        exclusive_by_tag = {}
    if cat_filter in ("专属", "exclusive", "excl"):
        non_exclusive = []

    # rebuild by_tag from filtered exclusive rows when tag filter applied
    if tag_f or cat_filter:
        from collections import defaultdict

        by_tag: dict = defaultdict(list)
        for r in exclusive:
            by_tag[str(r.get("tag") or "")].append(r)
        exclusive_by_tag = {k: v for k, v in sorted(by_tag.items()) if k}

    summary = cached.get("summary_text") or cached.get("text") or ""
    if cat_filter or tag_f:
        summary = (
            f"(derived filter category={category or '-'} tag={tag or '-'})\n" + summary
        )

    return {
        "ok": True,
        "source": "derived",
        "derived": derived,
        "stats": cached.get("stats"),
        "exclusive_by_tag": exclusive_by_tag,
        "non_exclusive": non_exclusive if not tag_f else [],
        "trees": rows,
        "summary_text": summary,
        "text": summary,
        "mod": cached.get("mod"),
    }


def tool_focus_derived(args: dict, ctx: ToolContext) -> dict:
    """读 database/derived/focus 预解析国策。"""
    import sys

    tool_root = Path(__file__).resolve().parents[1]
    repo = tool_root.parent
    if str(tool_root) not in sys.path:
        sys.path.insert(0, str(tool_root))
    from focus_topology.derived import (  # noqa: WPS433
        focus_derived_root,
        list_trees_for_tag,
        load_catalog_json,
        load_index,
        load_manifest,
        load_registry,
        load_tree_graph,
        resolve_derived_dir,
    )

    action = str(args.get("action") or "").strip().lower()
    mod = str(args.get("mod") or "").strip() or None

    if action == "list":
        reg = load_registry(repo)
        mods = reg.get("mods") or {}
        # de-dupe by dir
        by_dir: dict[str, dict] = {}
        for entry in mods.values():
            if isinstance(entry, dict) and entry.get("dir"):
                by_dir[str(entry["dir"])] = entry
        return {
            "ok": True,
            "derived_root": str(focus_derived_root(repo)),
            "mods": list(by_dir.values()),
            "hint": (
                "未列出的模组需先运行: "
                "python ingest/focus_corpus/build_focus_corpus.py --mod <short>"
            ),
        }

    derived = None
    if mod:
        derived = resolve_derived_dir(mod_short=mod, mod_id=mod, repo=repo)
    if derived is None:
        derived = resolve_derived_dir(game_root=ctx.game_root, repo=repo)
    if derived is None:
        return {
            "ok": False,
            "error": "derived_missing",
            "hint": (
                "当前语料尚无预解析国策。构建示例: "
                "python ingest/focus_corpus/build_focus_corpus.py --mod TFR"
            ),
            "derived_root": str(focus_derived_root(repo)),
            "game_root": str(ctx.game_root),
            "mod": mod,
        }

    if action == "catalog":
        data = load_catalog_json(derived)
        if not data:
            return {"ok": False, "error": "catalog_unreadable", "derived": str(derived)}
        out = dict(data)
        out["source"] = "derived"
        out["derived"] = str(derived)
        out["manifest"] = load_manifest(derived)
        return out

    if action == "index":
        index = load_index(derived)
        if not index:
            return {"ok": False, "error": "index_unreadable", "derived": str(derived)}
        tag = str(args.get("tag") or "").strip()
        trees = dict(index.get("trees") or {})
        if tag:
            want = set(list_trees_for_tag(derived, tag))
            trees = {k: v for k, v in trees.items() if k in want}
        return {
            "ok": True,
            "source": "derived",
            "derived": str(derived),
            "mod": index.get("mod"),
            "tree_count": len(trees),
            "trees": trees,
            "tag_filter": tag or None,
        }

    if action == "tree":
        tree_id = str(args.get("tree_id") or "").strip()
        if not tree_id:
            return {"ok": False, "error": "tree_id_required"}
        graph = load_tree_graph(derived, tree_id)
        if not graph:
            return {
                "ok": False,
                "error": "tree_not_found",
                "tree_id": tree_id,
                "derived": str(derived),
            }
        # slim for agent: keep structure, cap huge node dumps? keep full — agent needs coords
        return {
            "ok": True,
            "source": "derived",
            "derived": str(derived),
            "tree": graph,
        }

    return {
        "ok": False,
        "error": "bad_action",
        "hint": "action 须为 list | catalog | index | tree",
    }


def tool_focus_topology(args: dict, ctx: ToolContext) -> dict:
    """按 TAG 建 HOI4 国策拓扑（树 / 入口 / 互斥分支）。"""
    import sys

    tool_root = Path(__file__).resolve().parents[1]
    if str(tool_root) not in sys.path:
        sys.path.insert(0, str(tool_root))
    from focus_topology import focus_topology_for_tag  # noqa: WPS433

    tag = str(args.get("tag") or "").strip()
    tree_id = args.get("tree_id")
    tree_id_s = str(tree_id).strip() if tree_id else None
    try:
        depth = int(args.get("outline_depth") or 2)
    except (TypeError, ValueError):
        depth = 2
    depth = max(0, min(depth, 4))

    result = focus_topology_for_tag(
        ctx.game_root,
        tag,
        tree_id=tree_id_s or None,
        outline_depth=depth,
    )
    if not result.get("ok"):
        return result

    # 为入口/分叉补 loc 名（便于直接答题）
    name_ids: list[str] = []
    for t in result.get("trees") or []:
        name_ids.extend(t.get("local_roots") or [])
        name_ids.extend(t.get("attached_shared_roots") or [])
        for f in t.get("forks") or []:
            name_ids.extend(f.get("choices") or [])
            name_ids.extend(f.get("after") or [])
    loc_names = _focus_loc_names(ctx, name_ids)

    slim_trees = []
    for t in result.get("trees") or []:
        slim_trees.append(
            {
                "id": t.get("id"),
                "path": t.get("path"),
                "start_line": t.get("start_line"),
                "gate": t.get("gate"),
                "stats": t.get("stats"),
                "local_roots": t.get("local_roots"),
                "attached_shared_roots": t.get("attached_shared_roots"),
                "forks": t.get("forks"),
                "missing_shared": t.get("missing_shared"),
            }
        )

    summary = str(result.get("summary_text") or "")
    if loc_names:
        loc_lines = ["  loc_names:"]
        for kid, val in sorted(loc_names.items()):
            loc_lines.append(f"    {kid} = {val}")
        summary = summary + "\n" + "\n".join(loc_lines)

    return {
        "ok": True,
        "tag": result.get("tag"),
        "tree_id_filter": result.get("tree_id_filter"),
        "matched_tree_count": result.get("matched_tree_count"),
        "trees": slim_trees,
        "loc_names": loc_names,
        "summary_text": summary,
        "text": summary,
        "hint": (
            "问「几条路线」看 route_forks / forks，不要把 attached_shared_focus 算作派系路线。"
            "local_entry_focuses 才是本树本地入口。"
            "可将 text/summary_text 作为 submit 的 computation 证据；"
            "某国策效果再 read_block(path, key=focus_id, depth=any)。"
        ),
    }


def _focus_loc_names(ctx: ToolContext, ids: list[str]) -> dict[str, str]:
    """focus id → 简中名；缺库则空。"""
    uniq = list(dict.fromkeys(i for i in ids if i))
    if not uniq or not ctx.loc_db.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        conn = sqlite3.connect(str(ctx.loc_db))
        try:
            for kid in uniq[:80]:
                row = conn.execute(
                    """
                    SELECT value FROM localization
                    WHERE key=? AND lang=? LIMIT 1
                    """,
                    (kid, ctx.lang),
                ).fetchone()
                if row and row[0]:
                    out[kid] = str(row[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return out
    return out


def tool_registry_lookup(args: dict, ctx: ToolContext) -> dict:
    kind = str(args.get("kind") or "").strip()
    key = str(args.get("key") or "").strip()
    type_filter = args.get("type")
    mode = str(args.get("mode") or "exact")
    limit = int(args.get("limit") or 30)
    if not kind or not key:
        return {"ok": False, "error": "need_kind_and_key"}

    if kind == "common":
        db = ctx.game_root / "common_registry.sqlite"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            where = []
            params: list[Any] = []
            if mode == "exact":
                where.append("key=?")
                params.append(key)
            elif mode == "prefix":
                where.append("key LIKE ?")
                params.append(f"{key}%")
            else:
                where.append("key LIKE ?")
                params.append(f"%{key}%")
            if type_filter:
                where.append("type=?")
                params.append(str(type_filter))
            params.append(limit)
            sql = (
                f"SELECT key, file, type FROM entries WHERE {' AND '.join(where)} "
                f"ORDER BY length(key) ASC LIMIT ?"
            )
            rows = conn.execute(sql, params).fetchall()
            hits = []
            for r in rows:
                d = dict(r)
                d["path"] = f"common/{d['type']}/{d['file']}"
                hits.append(d)
            return {"ok": True, "hits": hits, "count": len(hits), "mode": mode}
        finally:
            conn.close()

    if kind == "events":
        db = ctx.game_root / "events_registry.sqlite"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT key, namespace, number, file FROM entries WHERE key=? LIMIT ?",
                (key, limit),
            ).fetchall()
            hits = []
            for r in rows:
                d = dict(r)
                resolved = _resolve_events_path(ctx.game_root, d["file"])
                if resolved:
                    d["path"] = resolved
                else:
                    d["path_hint"] = f"events/**/{d['file']}"
                hits.append(d)
            return {
                "ok": True,
                "hits": hits,
                "count": len(hits),
                "note": "未入表视为事件不存在",
            }
        finally:
            conn.close()

    if kind == "map":
        db = ctx.game_root / "map_data_registry.sqlite"
        if not db.is_file():
            return {
                "ok": True,
                "hits": [],
                "count": 0,
                "note": "map_data_registry.sqlite missing (not built for this corpus)",
            }
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT key, kind, file FROM entries WHERE key=? LIMIT ?",
                (key, limit),
            ).fetchall()
            hits = []
            for r in rows:
                d = dict(r)
                d["path"] = f"map_data/state_regions/{d['file']}"
                hits.append(d)
            return {"ok": True, "hits": hits, "count": len(hits)}
        finally:
            conn.close()

    if kind == "hub":
        db = ctx.game_root / "hub_anchors.sqlite"
        if not db.is_file():
            return {
                "ok": True,
                "hits": [],
                "count": 0,
                "note": "hub_anchors.sqlite missing (not built for this corpus)",
            }
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            # key 可为 STATE_X 或 STATE_X:city
            if ":" in key:
                state, slot = key.split(":", 1)
                rows = conn.execute(
                    "SELECT state_key, slot, province, kind, file FROM anchors "
                    "WHERE state_key=? AND slot=? LIMIT ?",
                    (state, slot, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT state_key, slot, province, kind, file FROM anchors "
                    "WHERE state_key=? LIMIT ?",
                    (key, limit),
                ).fetchall()
            return {"ok": True, "hits": [dict(r) for r in rows], "count": len(rows)}
        finally:
            conn.close()

    return {"ok": False, "error": f"unknown_kind:{kind}"}


def tool_read_block(args: dict, ctx: ToolContext) -> dict:
    path = str(args.get("path") or "").strip()
    key = str(args.get("key") or "").strip()
    depth = str(args.get("depth") or "top")
    include_text = bool(args.get("include_text", True))
    if not path or not key:
        return {"ok": False, "error": "need_path_and_key"}
    resolved = _resolve_corpus_path(ctx, path)
    if isinstance(resolved, dict):
        return resolved
    fp, root, rel = resolved
    if not rel:
        return {"ok": False, "error": "need_file_path"}
    result = _read_block(rel, key, depth_mode=depth, game_root=root)
    if isinstance(result, dict) and result.get("ok") is not False:
        result = dict(result)
        result["path"] = path if path.startswith("mods/") else result.get("path", path)
    if not include_text:
        result = dict(result)
        result.pop("text", None)
        for h in result.get("hits") or []:
            h.pop("text", None)
        return result
    # 预览截断，完整 text 提交时再 hydrate
    result = dict(result)
    if result.get("text") and len(result["text"]) > _TEXT_PREVIEW:
        full_len = len(result["text"])
        result["text"] = result["text"][:_TEXT_PREVIEW] + f"\n…[truncated preview, full={full_len}]"
        result["text_truncated"] = True
    if result.get("ambiguous") and result.get("hits"):
        for h in result["hits"]:
            if h.get("text") and len(h["text"]) > _TEXT_PREVIEW:
                h["text"] = h["text"][:_TEXT_PREVIEW] + "\n…[truncated]"
    return result


def tool_read_lines(args: dict, ctx: ToolContext) -> dict:
    path = str(args.get("path") or "").strip()
    try:
        start = int(args.get("start_line"))
        end = int(args.get("end_line"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad_line_range"}
    if not path:
        return {"ok": False, "error": "empty_path"}
    if start < 1 or end < start:
        return {"ok": False, "error": "invalid_range"}
    if end - start > 200:
        return {"ok": False, "error": "range_too_large_max_200_lines"}

    resolved = _resolve_corpus_path(ctx, path)
    if isinstance(resolved, dict):
        return resolved
    fp, _root, _rel = resolved
    if not fp.is_file():
        return {"ok": False, "error": "file_not_found", "path": path}
    try:
        lines = fp.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    chunk = []
    for i in range(start, min(end, len(lines)) + 1):
        chunk.append(f"{i}|{lines[i - 1]}")
    return {
        "ok": True,
        "path": path,
        "start_line": start,
        "end_line": min(end, len(lines)),
        "total_lines": len(lines),
        "text": "\n".join(chunk),
    }


@dataclass
class _GrepScanResult:
    hits: list[dict]
    truncated: bool
    stop_reason: str | None = None
    files_scanned: int = 0
    bytes_scanned: int = 0
    scanned_hint: str | None = None
    engine: str = "python"


def _grep_glob_too_broad(glob_s: str) -> str | None:
    """过宽 glob 直接拒绝，逼 Agent 带目录前缀（对标 Cursor 少扫垃圾树）。"""
    g = (glob_s or "").strip().replace("\\", "/")
    if not g:
        return "glob_required：请指定带顶层目录的 glob（如 common/**/*.txt），禁止省略"
    # 去掉可选的 mods/<id>/ 前缀再判断
    rest = g
    if rest == "mods" or rest.startswith("mods/"):
        parts = rest.split("/", 2)
        if len(parts) < 3:
            return "glob_too_broad：整池请写 mods/<id>/<top>/...（如 mods/2438003901/common/**/*.txt）"
        rest = parts[2]
    bare = rest.lstrip("./")
    if bare.startswith("**/") or bare in {"**", "**/*", "**/*.txt", "**/*.*", "*.txt", "*"}:
        return (
            "glob_too_broad：禁止裸 **/*.txt / *.txt；"
            "请加顶层目录，如 events/**/*.txt 或 common/decisions/**/*.txt"
        )
    top = bare.split("/", 1)[0].split("*", 1)[0]
    if not top:
        return "glob_too_broad：无法解析顶层目录"
    return None


def _iter_file_lines_budgeted(
    fp: Path,
    *,
    max_file_bytes: int,
) -> Iterator[tuple[int, str, int]]:
    """按行读取；(line_no, line_without_newline, raw_byte_len)。单文件超限即停。"""
    read_n = 0
    with fp.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for i, line in enumerate(f, 1):
            raw_len = len(line.encode("utf-8", errors="replace"))
            read_n += raw_len
            yield i, line.rstrip("\r\n"), raw_len
            if read_n >= max_file_bytes:
                return


def _grep_files_python(
    files: list[Path],
    *,
    rx: re.Pattern[str],
    rel_of: Callable[[Path], str],
    max_hits: int,
) -> _GrepScanResult:
    hits: list[dict] = []
    bytes_scanned = 0
    files_scanned = 0
    hint: str | None = None
    for fp in files:
        if files_scanned >= _GREP_MAX_FILES:
            return _GrepScanResult(
                hits=hits,
                truncated=True,
                stop_reason="max_files",
                files_scanned=files_scanned,
                bytes_scanned=bytes_scanned,
                scanned_hint=hint,
                engine="python",
            )
        if bytes_scanned >= _GREP_MAX_BYTES:
            return _GrepScanResult(
                hits=hits,
                truncated=True,
                stop_reason="max_bytes",
                files_scanned=files_scanned,
                bytes_scanned=bytes_scanned,
                scanned_hint=hint,
                engine="python",
            )
        try:
            if not fp.is_file():
                continue
        except OSError:
            continue
        files_scanned += 1
        rel = rel_of(fp)
        hint = rel
        try:
            for line_no, line, raw_len in _iter_file_lines_budgeted(
                fp, max_file_bytes=_GREP_MAX_FILE_BYTES
            ):
                bytes_scanned += raw_len
                if rx.search(line):
                    hits.append(
                        {
                            "path": rel,
                            "line": line_no,
                            "text": line[:_GREP_LINE_MAX],
                        }
                    )
                    if len(hits) >= max_hits:
                        return _GrepScanResult(
                            hits=hits,
                            truncated=True,
                            stop_reason="max_hits",
                            files_scanned=files_scanned,
                            bytes_scanned=bytes_scanned,
                            scanned_hint=rel,
                            engine="python",
                        )
                if bytes_scanned >= _GREP_MAX_BYTES:
                    return _GrepScanResult(
                        hits=hits,
                        truncated=True,
                        stop_reason="max_bytes",
                        files_scanned=files_scanned,
                        bytes_scanned=bytes_scanned,
                        scanned_hint=rel,
                        engine="python",
                    )
        except OSError:
            continue
    return _GrepScanResult(
        hits=hits,
        truncated=False,
        stop_reason=None,
        files_scanned=files_scanned,
        bytes_scanned=bytes_scanned,
        scanned_hint=hint,
        engine="python",
    )


def _resolve_rg_bin() -> str | None:
    """系统 ripgrep；跳过 Cursor/@vscode 自带 rg（glob 语义不一致，易空命中）。"""
    for cand in ("/usr/bin/rg", "/usr/local/bin/rg"):
        if Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    rg = shutil.which("rg")
    if not rg:
        return None
    norm = rg.replace("\\", "/")
    if "node_modules" in norm or "@vscode/ripgrep" in norm:
        return None
    return rg


def _grep_with_rg(
    *,
    root: Path,
    pattern: str,
    glob_pat: str | None,
    max_hits: int,
    literal: bool,
    path_prefix: str = "",
    scan_tops: tuple[str, ...] | list[str] | None = None,
) -> _GrepScanResult | None:
    """若本机有标准 rg：流式搜。失败返回 None → 调用方走 Python。"""
    rg = _resolve_rg_bin()
    if not rg:
        return None
    root = root.resolve()
    if not root.is_dir():
        return None
    cmd: list[str] = [
        rg,
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--no-messages",
        "--max-filesize",
        str(_GREP_MAX_FILE_BYTES),
        "--max-count",
        str(max_hits),
    ]
    if literal:
        cmd.append("--fixed-strings")
    allowed = list(scan_tops) if scan_tops is not None else list(DEFAULT_SCAN_TOPS)
    g = (glob_pat or "").strip()
    if g:
        cmd.extend(["--glob", g])
    else:
        for top in allowed:
            cmd.extend(["--glob", f"{top}/**"])
    for bad in ("*.sqlite", "*.sqlite-*", "*.png", "*.dds", "*.bin"):
        cmd.extend(["--glob", f"!{bad}"])
    # 必须 cwd=root 再搜「.」：rg 的 --glob 相对 cwd 匹配；对绝对路径传 root
    # 时，形如 common/**/*.txt 的 glob 会整树空命中（本次 VPS 冒烟已踩）。
    cmd.extend(["--", pattern, "."])
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_GREP_RG_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):
        return None
    hits: list[dict] = []
    files_seen: set[str] = set()
    for raw in (proc.stdout or "").splitlines():
        # path:line:text — 从左侧拆路径较麻烦（路径可含冒号）；用 rg --json 更稳，这里用 rsplit
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        path_part, line_s, text = parts[0], parts[1], parts[2]
        try:
            line_no = int(line_s)
        except ValueError:
            continue
        # cwd=root 时常见 ./common/... 或 common/...
        path_part = path_part.lstrip("./")
        abs_p = Path(path_part)
        try:
            if not abs_p.is_absolute():
                abs_p = (root / path_part).resolve()
            rel = abs_p.resolve().relative_to(root).as_posix()
        except Exception:
            rel = path_part.replace("\\", "/")
            if rel.startswith(str(root).replace("\\", "/") + "/"):
                rel = rel[len(str(root).replace("\\", "/")) + 1 :]
        top = rel.split("/", 1)[0] if rel else ""
        if top and top not in allowed and not path_prefix:
            continue
        out_path = f"{path_prefix}{rel}" if path_prefix else rel
        files_seen.add(out_path)
        hits.append(
            {
                "path": out_path,
                "line": line_no,
                "text": text[:_GREP_LINE_MAX],
            }
        )
        if len(hits) >= max_hits:
            return _GrepScanResult(
                hits=hits,
                truncated=True,
                stop_reason="max_hits",
                files_scanned=len(files_seen),
                bytes_scanned=0,
                scanned_hint=out_path,
                engine="rg",
            )
    return _GrepScanResult(
        hits=hits,
        truncated=False,
        stop_reason=None,
        files_scanned=len(files_seen),
        bytes_scanned=0,
        scanned_hint=hits[-1]["path"] if hits else None,
        engine="rg",
    )


def _grep_result_payload(scan: _GrepScanResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "hits": scan.hits,
        "count": len(scan.hits),
        "truncated": scan.truncated,
        "engine": scan.engine,
        "files_scanned": scan.files_scanned,
        "bytes_scanned": scan.bytes_scanned,
    }
    if scan.scanned_hint:
        out["scanned_hint"] = scan.scanned_hint
    if scan.stop_reason:
        out["stop_reason"] = scan.stop_reason
    if scan.truncated and scan.stop_reason in {"max_bytes", "max_files"}:
        out["hint"] = (
            "读盘预算已用尽（类 ripgrep 上限）。请收窄 glob 到具体子目录/文件名后重试，"
            "不要使用 **/*.txt。"
        )
    return out


def tool_grep_text(args: dict, ctx: ToolContext) -> dict:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return {"ok": False, "error": "empty_pattern"}
    glob_pat = args.get("glob")
    max_hits = min(int(args.get("max_hits") or 30), _GREP_MAX_HITS)
    literal = bool(args.get("literal", False))
    try:
        rx = re.compile(re.escape(pattern) if literal else pattern)
    except re.error as e:
        return {"ok": False, "error": f"bad_regex: {e}"}

    glob_s = str(glob_pat or "").strip()
    broad = _grep_glob_too_broad(glob_s)
    if broad:
        return {"ok": False, "error": broad}

    # 整池：glob 以 mods/ 开头时，在对应模组树内搜
    if glob_s.startswith("mods/") or glob_s == "mods":
        resolved = _resolve_corpus_path(ctx, glob_s.split("*", 1)[0].rstrip("/") or "mods")
        if isinstance(resolved, dict):
            return resolved
        p = _normalize_rel_path(glob_s)
        rest = "" if p == "mods" else p[5:]
        if rest and "/" in rest.split("*", 1)[0]:
            mid = rest.split("/", 1)[0]
            if mid not in ctx.visible_mod_ids:
                return {"ok": False, "error": "mod_not_in_pool", "mod_id": mid}
            if ctx.mods_root is None:
                return {"ok": False, "error": "mods_not_mounted"}
            mod_root = (ctx.mods_root / mid).resolve()
            sub = rest[len(mid) :].lstrip("/")
            prefix = f"mods/{mid}/"
            rg_scan = _grep_with_rg(
                root=mod_root,
                pattern=pattern,
                glob_pat=sub if sub else None,
                max_hits=max_hits,
                literal=literal,
                path_prefix=prefix,
                scan_tops=ctx.scan_tops,
            )
            if rg_scan is not None:
                return _grep_result_payload(rg_scan)
            files = _iter_game_files(
                mod_root, sub if sub else None, scan_tops=ctx.scan_tops
            )
            scan = _grep_files_python(
                files,
                rx=rx,
                rel_of=lambda fp, _mr=mod_root, _pf=prefix: _pf
                + fp.relative_to(_mr).as_posix(),
                max_hits=max_hits,
            )
            return _grep_result_payload(scan)
        return {
            "ok": False,
            "error": (
                "glob_too_broad：整池请写 mods/<id>/<top>/...，"
                "不要只写 mods/ 或 mods/*"
            ),
        }

    root = ctx.game_root.resolve()
    rg_scan = _grep_with_rg(
        root=root,
        pattern=pattern,
        glob_pat=glob_s or None,
        max_hits=max_hits,
        literal=literal,
        scan_tops=ctx.scan_tops,
    )
    if rg_scan is not None:
        return _grep_result_payload(rg_scan)
    files = _iter_game_files(root, glob_pat, scan_tops=ctx.scan_tops)
    scan = _grep_files_python(
        files,
        rx=rx,
        rel_of=lambda fp, _r=root: fp.relative_to(_r).as_posix(),
        max_hits=max_hits,
    )
    return _grep_result_payload(scan)



def tool_run_code(args: dict, ctx: ToolContext) -> dict:
    code = str(args.get("code") or "")
    timeout = float(args.get("timeout_sec") or 8)
    if ctx.sandbox is None:
        return {"ok": False, "error": "no_sandbox"}
    r = ctx.sandbox.run_code(code, timeout_sec=timeout)
    hint = "game files at /game ; write extras under /work/out/"
    if ctx.mods_root is not None:
        hint = (
            "vanilla at /game ; pool mods at /mods/<id>/ when mounted ; "
            "write extras under /work/out/"
        )
    return {
        "ok": r.ok,
        "exit_code": r.exit_code,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "timed_out": r.timed_out,
        "elapsed_sec": round(r.elapsed_sec, 3),
        "truncated": r.truncated,
        "error": r.error,
        "hint": hint,
    }


def _loc_row(r: dict) -> dict:
    out = {
        "key": r.get("key"),
        "lang": r.get("lang"),
        "value": r.get("value"),
        "source_file": r.get("source_file"),
    }
    if "value_expanded" in r:
        out["value_expanded"] = r["value_expanded"]
    if "score" in r:
        out["score"] = r["score"]
    return out


def _country_name_candidates(ctx: ToolContext, rows: list[dict]) -> list[dict]:
    """从 loc 命中里挑可能是国家 tag 的候选（去 _ADJ / dyn_ 等噪声）。"""
    tags: list[str] = []
    for r in rows:
        k = str(r.get("key") or "")
        if not k or k.endswith("_ADJ"):
            continue
        if k.startswith("dyn_"):
            continue
        if k.startswith("ethnicity_") or k.startswith("geographic_"):
            continue
        # 典型 tag：全大写短串，或已在 country_definitions
        if re.fullmatch(r"[A-Z0-9]{2,4}", k):
            tags.append(k)
    if not tags:
        return []
    db = ctx.game_root / "common_registry.sqlite"
    if not db.is_file():
        return [{"key": t} for t in dict.fromkeys(tags)]
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    out: list[dict] = []
    try:
        for t in dict.fromkeys(tags):
            rows = conn.execute(
                "SELECT key, file, type FROM entries WHERE key=? AND type='country_definitions'",
                (t,),
            ).fetchall()
            if rows:
                for row in rows:
                    d = dict(row)
                    d["path"] = f"common/{d['type']}/{d['file']}"
                    out.append(d)
            else:
                out.append({"key": t, "note": "not_in_country_definitions"})
    finally:
        conn.close()
    return out


def _iter_game_files(
    root: Path,
    glob_pat: str | None,
    *,
    scan_tops: tuple[str, ...] | list[str] | None = None,
) -> list[Path]:
    """扫 scan_tops 下文本（默认 Vic3：common / events / map_data）。"""
    allowed = list(scan_tops) if scan_tops is not None else list(DEFAULT_SCAN_TOPS)
    text_suffix = {
        ".txt",
        ".csv",
        ".md",
        ".json",
        ".yml",
        ".yaml",
        ".tsv",
        ".gui",
        ".gfx",
        ".lua",
    }
    out: list[Path] = []
    if glob_pat:
        for p in root.glob(glob_pat):
            if not p.is_file():
                continue
            try:
                rel = p.resolve().relative_to(root)
            except ValueError:
                continue
            top = rel.parts[0] if rel.parts else ""
            if top not in allowed:
                continue
            if p.suffix.lower() in {".sqlite", ".png", ".dds"}:
                continue
            out.append(p)
        return out

    for top in allowed:
        base = root / top
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in text_suffix:
                out.append(p)
    return out

def dumps_tool_result(obj: Any, *, limit: int = 28_000) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n…[truncated tool result {len(s) - limit} chars]"


def _resolve_events_path(game_root: Path, filename: str) -> str | None:
    base = game_root / "events"
    if not base.is_dir():
        return None
    # 先直接相对路径
    direct = base / filename
    if direct.is_file():
        return direct.relative_to(game_root).as_posix()
    matches = list(base.rglob(filename))
    if len(matches) == 1:
        return matches[0].relative_to(game_root).as_posix()
    if matches:
        # 多命中：返回最短相对路径
        matches.sort(key=lambda p: len(p.as_posix()))
        return matches[0].relative_to(game_root).as_posix()
    return None
