"""模组推荐 JSONL 日志读取（控制台用）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DAY_FILE_RE = re.compile(r"^(\d{8})\.jsonl$")


@dataclass
class RecommendLogRow:
    request_id: str
    time_bj: str
    date_bj: str
    ts_unix: float
    mode: str
    query: str
    query_rewritten: str | None
    used_rewrite: bool
    rewrite_error: str | None
    filters: dict[str, Any]
    refused: bool
    refuse_reason: str | None
    recalled: list[dict[str, str]]
    passed: list[dict[str, str]]
    present: dict[str, Any] | None
    status_zh: str
    result_excerpt: str


def list_recommend_log_dates(log_dir: Path) -> list[str]:
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return []
    out: list[str] = []
    for p in log_dir.glob("????????.jsonl"):
        m = _DAY_FILE_RE.match(p.name)
        if not m:
            continue
        s = m.group(1)
        out.append(f"{s[0:4]}-{s[4:6]}-{s[6:8]}")
    return sorted(set(out), reverse=True)


def _day_paths(log_dir: Path, *, date_yyyymmdd: str | None) -> list[Path]:
    log_dir = Path(log_dir)
    if date_yyyymmdd:
        p = log_dir / f"{date_yyyymmdd}.jsonl"
        return [p] if p.is_file() else []
    return sorted(log_dir.glob("????????.jsonl"), reverse=True)


def _parse_date_param(date: str | None) -> str | None:
    if not date:
        return None
    s = date.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s.replace("-", "")
    if len(s) == 8 and s.isdigit():
        return s
    raise ValueError("date 须为 YYYY-MM-DD 或 YYYYMMDD")


def _status_and_excerpt(rec: dict[str, Any]) -> tuple[str, str]:
    if rec.get("refused"):
        reason = str(rec.get("refuse_reason") or "已拒绝")
        return "已拒绝", reason
    passed = rec.get("passed") or []
    recalled = rec.get("recalled") or []
    n_pass = len(passed)
    n_rec = len(recalled)
    if n_pass == 0:
        return "无结果", f"召回 {n_rec} · 通过 0"
    titles = [str(x.get("title") or x.get("id") or "") for x in passed[:3]]
    titles = [t for t in titles if t]
    head = "、".join(titles)
    if n_pass > 3:
        head += f" 等 {n_pass} 个"
    return "完成", f"召回 {n_rec} · 通过 {n_pass}" + (f"：{head}" if head else "")


def _row_from_record(rec: dict[str, Any]) -> RecommendLogRow | None:
    rid = str(rec.get("id") or "").strip()
    if not rid:
        return None
    status_zh, excerpt = _status_and_excerpt(rec)
    date_bj = str(rec.get("date_bj") or "")
    time_bj = str(rec.get("time_bj") or "")
    if not date_bj and time_bj:
        date_bj = time_bj[:10]
    return RecommendLogRow(
        request_id=rid,
        time_bj=time_bj,
        date_bj=date_bj,
        ts_unix=float(rec.get("ts") or 0.0),
        mode=str(rec.get("mode") or "recommend"),
        query=str(rec.get("query") or ""),
        query_rewritten=rec.get("query_rewritten"),
        used_rewrite=bool(rec.get("used_rewrite")),
        rewrite_error=rec.get("rewrite_error"),
        filters=dict(rec.get("filters") or {}),
        refused=bool(rec.get("refused")),
        refuse_reason=rec.get("refuse_reason"),
        recalled=list(rec.get("recalled") or []),
        passed=list(rec.get("passed") or []),
        present=rec.get("present") if isinstance(rec.get("present"), dict) else None,
        status_zh=status_zh,
        result_excerpt=excerpt,
    )


def load_recent_recommend(
    log_dir: Path,
    *,
    limit: int = 200,
    date: str | None = None,
) -> tuple[list[RecommendLogRow], int]:
    date_key = _parse_date_param(date)
    paths = _day_paths(Path(log_dir), date_yyyymmdd=date_key)
    rows: list[RecommendLogRow] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            row = _row_from_record(rec)
            if row:
                rows.append(row)
    rows.sort(key=lambda r: r.ts_unix, reverse=True)
    total = len(rows)
    return rows[: max(1, limit)], total


def load_recommend_detail(log_dir: Path, request_id: str) -> dict[str, Any] | None:
    rid = (request_id or "").strip()
    if not rid or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", rid):
        return None
    # 扫全部日子找 id（量小；也可日后加 by_id 索引）
    for path in sorted(Path(log_dir).glob("????????.jsonl"), reverse=True):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or rid not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and str(rec.get("id") or "") == rid:
                row = _row_from_record(rec)
                if not row:
                    return None
                return {
                    "ok": True,
                    "request_id": row.request_id,
                    "time": row.time_bj,
                    "date": row.date_bj,
                    "mode": row.mode,
                    "query": row.query,
                    "query_rewritten": row.query_rewritten,
                    "used_rewrite": row.used_rewrite,
                    "rewrite_error": row.rewrite_error,
                    "filters": row.filters,
                    "refused": row.refused,
                    "refuse_reason": row.refuse_reason,
                    "recalled": row.recalled,
                    "passed": row.passed,
                    "present": row.present,
                    "status_zh": row.status_zh,
                }
    return None
