"""1936 submit hydrate / JSON 修复（服务侧，非通用壳）。"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from evidence import (
    COMPUTATION_ENTITY_TYPE,
    EvidenceItem,
    EvidencePackage,
    GapItem,
    ResolvedEntity,
)
from read_block import read_block

def _parse_tool_arguments(raw: str) -> tuple[dict | None, str | None]:
    """解析工具 arguments；对 submit 常见坏 JSON 做修复/骨架恢复。"""
    s = (raw or "").strip()
    if not s:
        return None, "empty_arguments"

    obj, err = _try_json_object(s)
    if obj is not None:
        return obj, None

    repaired = _repair_json_string(s)
    if repaired != s:
        obj, err2 = _try_json_object(repaired)
        if obj is not None:
            return obj, None
        err = err2 or err

    recovered = _recover_submit_skeleton(s)
    if recovered is not None:
        return recovered, None

    return None, err or "invalid_json"


def _try_json_object(s: str) -> tuple[dict | None, str | None]:
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        return None, f"{e.msg} at pos {e.pos}"
    if not isinstance(obj, dict):
        return None, "not_object"
    return obj, None


def _repair_json_string(s: str) -> str:
    """常见修复：弯引号、字符串值内未转义的直双引号。"""
    out = (
        s.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    # 在 JSON 字符串值内部，把未转义 " 改成 '
    return _escape_inner_quotes(out)


def _escape_inner_quotes(s: str) -> str:
    """状态机：键与结构上的引号保留；值内部的裸 " → '。"""
    chars: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if escape:
            chars.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\" and in_string:
            chars.append(ch)
            escape = True
            i += 1
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                chars.append(ch)
                i += 1
                continue
            # 可能是字符串结束，或值内非法引号
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j >= n or s[j] in ",}]:" :
                in_string = False
                chars.append(ch)
            else:
                chars.append("'")
            i += 1
            continue
        chars.append(ch)
        i += 1
    return "".join(chars)


def _recover_submit_skeleton(raw: str) -> dict | None:
    """从损坏的 submit arguments 里捞 coverage + path/key 列表。"""
    if "coverage" not in raw and "items" not in raw:
        return None
    cov_m = re.search(r'"coverage"\s*:\s*"(sufficient|partial|empty|ambiguous)"', raw)
    items: list[dict] = []
    for m in re.finditer(
        r'"path"\s*:\s*"([^"\\]+)"\s*,\s*"key"\s*:\s*"([^"\\]+)"',
        raw,
    ):
        items.append({"path": m.group(1), "key": m.group(2), "role": "primary"})
    if not items:
        for m in re.finditer(
            r'"key"\s*:\s*"([^"\\]+)"\s*,\s*"path"\s*:\s*"([^"\\]+)"',
            raw,
        ):
            items.append({"path": m.group(2), "key": m.group(1), "role": "primary"})
    if not cov_m and not items:
        return None
    out: dict[str, Any] = {
        "coverage": cov_m.group(1) if cov_m else ("partial" if items else "empty"),
        "items": items,
        "notes": "recovered_from_broken_submit_json",
    }
    # resolved_entities 尽力捞
    ents: list[dict] = []
    for m in re.finditer(
        r'"query"\s*:\s*"([^"\\]+)"\s*,\s*"key"\s*:\s*"([^"\\]+)"',
        raw,
    ):
        ents.append({"query": m.group(1), "key": m.group(2)})
    if ents:
        out["resolved_entities"] = ents
    return out



def _hydrate_submit(
    payload: dict,
    *,
    question: str,
    game: Path,
    mods_root: Path | None = None,
    visible_mod_ids: frozenset[str] | None = None,
) -> EvidencePackage:
    items_raw = payload.get("items") or []
    items: list[EvidenceItem] = []
    read_failures: list[GapItem] = []
    for raw in items_raw:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        key = str(raw.get("key") or "").strip()
        depth = str(raw.get("depth") or "top")
        text = str(raw.get("text") or "")
        entity_type = raw.get("entity_type")
        meta = {
            "role": raw.get("role") or "support",
            "entity_type": entity_type,
            "why": raw.get("why"),
            "label": raw.get("label"),
        }
        # 计算结果 / 显式正文：直接入包（不必 path+key）
        if text.strip():
            if not path and str(entity_type or "") == COMPUTATION_ENTITY_TYPE:
                path = "run_code"
            if not key and str(entity_type or "") == COMPUTATION_ENTITY_TYPE:
                key = "computation_result"
            if str(entity_type or "") == COMPUTATION_ENTITY_TYPE and meta["role"] == "support":
                meta["role"] = "primary"
            items.append(
                EvidenceItem.from_dict(
                    {
                        "path": path or "run_code",
                        "key": key or None,
                        "text": text,
                        "start_line": raw.get("start_line"),
                        "end_line": raw.get("end_line"),
                        **meta,
                    }
                )
            )
            continue
        if not path or not key:
            continue
        # pool：mods/<id>/... 用该模组根作 game_root
        read_root = game
        read_rel = path
        if path.startswith("mods/") or path.startswith("/mods/"):
            p = path.lstrip("/")
            rest = p[5:] if p.startswith("mods/") else p
            parts = rest.split("/", 1)
            mid = parts[0].strip()
            if not mid or mid not in (visible_mod_ids or frozenset()):
                read_failures.append(
                    GapItem(
                        reason=f"read_block 模组不在池内: {mid}",
                        key=key,
                        query=path,
                    )
                )
                continue
            if mods_root is None:
                read_failures.append(
                    GapItem(reason="mods_not_mounted", key=key, query=path)
                )
                continue
            read_root = (Path(mods_root) / mid).resolve()
            read_rel = parts[1] if len(parts) > 1 else ""
            if not read_rel:
                read_failures.append(
                    GapItem(reason="need_file_path_under_mod", key=key, query=path)
                )
                continue
        block = read_block(read_rel, key, depth_mode=depth, game_root=read_root)
        if not block.get("ok"):
            read_failures.append(
                GapItem(
                    reason=f"read_block 未取到正文: {block.get('error')}",
                    key=key,
                    query=path,
                )
            )
            continue
        if block.get("ambiguous"):
            hit = (block.get("hits") or [{}])[0]
            items.append(
                EvidenceItem.from_dict(
                    {
                        "path": path if path.startswith("mods/") else (hit.get("path") or path),
                        "key": key,
                        "text": hit.get("text") or "",
                        "start_line": hit.get("start_line"),
                        "end_line": hit.get("end_line"),
                        **meta,
                    }
                )
            )
        else:
            items.append(
                EvidenceItem.from_dict(
                    {
                        "path": path if path.startswith("mods/") else (block.get("path") or path),
                        "key": key,
                        "text": block.get("text") or "",
                        "start_line": block.get("start_line"),
                        "end_line": block.get("end_line"),
                        **meta,
                    }
                )
            )

    notes = str(payload["notes"]) if payload.get("notes") is not None else None
    notes_s = (notes or "").strip()
    cov = str(payload.get("coverage") or "partial")
    text_items = [i for i in items if i.text.strip()]

    # 算过了却只写在 notes / path+key 全失败：回填为 computation，避免答侧 empty 拒答
    if not text_items and notes_s and cov in ("sufficient", "partial"):
        text_items = [
            EvidenceItem(
                path="run_code",
                key="computation_result",
                text=notes_s,
                role="primary",
                entity_type=COMPUTATION_ENTITY_TYPE,
                why="沙箱/聚合结果（由 notes 回填为答用证据）",
            )
        ]
        read_failures = []

    if cov == "sufficient" and not text_items:
        cov = "empty"

    resolved = [
        ResolvedEntity.from_dict(x)
        for x in (payload.get("resolved_entities") or [])
        if isinstance(x, dict)
    ]
    unresolved = [
        GapItem.from_dict(x)
        for x in (payload.get("unresolved") or [])
        if isinstance(x, dict)
    ]
    unresolved.extend(read_failures)
    not_expanded = [
        GapItem.from_dict(x)
        for x in (payload.get("not_expanded") or [])
        if isinstance(x, dict)
    ]

    return EvidencePackage(
        coverage=cov,  # type: ignore[arg-type]
        items=text_items,
        question=question,
        question_focus=(
            str(payload["question_focus"])
            if payload.get("question_focus") is not None
            else None
        ),
        resolved_entities=resolved,
        unresolved=unresolved,
        not_expanded=not_expanded,
        notes=notes,
    )


def _write_log(payload: dict, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    sid = payload.get("session_id") or uuid.uuid4().hex[:8]
    path = log_dir / f"find_{ts}_{sid}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
