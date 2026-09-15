"""找侧工具实现 + OpenAI/DeepSeek function schemas。"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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
        "在文本树中搜索正则/子串（只读）。默认扫 /game；整池可用 glob=mods/<id>/**/*.txt。",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {
                    "type": "string",
                    "description": "可选，如 common/ideologies/*.txt 或 mods/<id>/common/**/*.txt",
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


@dataclass
class ToolContext:
    game_root: Path
    loc_db: Path
    sandbox: Any  # SandboxSession | None
    lang: str = "simp_chinese"
    # pool：host 上 _mods_view（id→削减树）
    mods_root: Path | None = None
    visible_mod_ids: frozenset[str] = field(default_factory=frozenset)


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
    # 整池：glob 以 mods/ 开头时，在对应模组树内搜
    if glob_s.startswith("mods/") or glob_s == "mods":
        resolved = _resolve_corpus_path(ctx, glob_s.split("*", 1)[0].rstrip("/") or "mods")
        if isinstance(resolved, dict):
            return resolved
        # 简化：若 glob 是 mods/<id>/... 取该 mod root；否则扫全部可见模组
        p = _normalize_rel_path(glob_s)
        rest = "" if p == "mods" else p[5:]
        roots: list[tuple[Path, str]] = []
        if rest and "/" in rest.split("*", 1)[0]:
            mid = rest.split("/", 1)[0]
            if mid not in ctx.visible_mod_ids:
                return {"ok": False, "error": "mod_not_in_pool", "mod_id": mid}
            if ctx.mods_root is None:
                return {"ok": False, "error": "mods_not_mounted"}
            mod_root = (ctx.mods_root / mid).resolve()
            sub = rest[len(mid) :].lstrip("/")
            # strip glob wildcards for _iter base — use mod_root + remaining glob
            files = _iter_game_files(mod_root, sub if sub else None)
            prefix = f"mods/{mid}/"
            hits: list[dict] = []
            for fp in files:
                try:
                    text = fp.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                rel = prefix + fp.relative_to(mod_root).as_posix()
                for i, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        hits.append(
                            {"path": rel, "line": i, "text": line[:_GREP_LINE_MAX]}
                        )
                        if len(hits) >= max_hits:
                            return {
                                "ok": True,
                                "hits": hits,
                                "truncated": True,
                                "scanned_hint": rel,
                            }
            return {"ok": True, "hits": hits, "truncated": False, "count": len(hits)}
        # mods 或 mods/* → 各可见模组各扫一遍
        if ctx.mods_root is None:
            return {"ok": False, "error": "mods_not_mounted"}
        hits = []
        for mid in sorted(ctx.visible_mod_ids):
            mod_root = (ctx.mods_root / mid).resolve()
            if not mod_root.is_dir():
                continue
            for fp in _iter_game_files(mod_root, None):
                try:
                    text = fp.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                rel = f"mods/{mid}/" + fp.relative_to(mod_root).as_posix()
                for i, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        hits.append(
                            {"path": rel, "line": i, "text": line[:_GREP_LINE_MAX]}
                        )
                        if len(hits) >= max_hits:
                            return {
                                "ok": True,
                                "hits": hits,
                                "truncated": True,
                                "scanned_hint": rel,
                            }
        return {"ok": True, "hits": hits, "truncated": False, "count": len(hits)}

    root = ctx.game_root.resolve()
    files = _iter_game_files(root, glob_pat)
    hits = []
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        rel = fp.relative_to(root).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(
                    {
                        "path": rel,
                        "line": i,
                        "text": line[:_GREP_LINE_MAX],
                    }
                )
                if len(hits) >= max_hits:
                    return {
                        "ok": True,
                        "hits": hits,
                        "truncated": True,
                        "scanned_hint": rel,
                    }
    return {"ok": True, "hits": hits, "truncated": False, "count": len(hits)}


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


def _iter_game_files(root: Path, glob_pat: str | None) -> list[Path]:
    """只扫 common / events / map_data 文本。"""
    allowed = ["common", "events", "map_data"]
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
        for p in base.rglob("*.txt"):
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
