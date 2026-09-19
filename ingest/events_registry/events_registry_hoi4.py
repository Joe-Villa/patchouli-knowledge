#!/usr/bin/env python3
"""HOI4 events 注册表：抽取 id = namespace.number。

输出 schema 与 Vic3 events_registry 相同，便于共用工具。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from events_registry import (  # noqa: E402
    EventsBuildResult,
    NS_DECL_RE,
    parse_event_key,
    prepare_content,
)

ADD_NS_RE = re.compile(r"(?m)^[ \t]*add_namespace[ \t]*=[ \t]*([^\s#]+)")
# HOI4：id = lar_spain.1（在 country_event / news_event 等块内）
INNER_ID_RE = re.compile(
    r"(?m)^[ \t]*id[ \t]*=[ \t]*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)\b"
)


def build_events_registry_hoi4(events_dir: Path) -> EventsBuildResult:
    if not events_dir.is_dir():
        raise FileNotFoundError(f"events 不存在: {events_dir}")

    entries: list[tuple[str, str, str, str]] = []
    rejected: list[dict] = []
    files_meta: list[dict] = []
    ns_declared: set[str] = set()
    seen_keys: set[str] = set()

    for path in sorted(events_dir.rglob("*.txt")):
        rel = path.relative_to(events_dir).as_posix()
        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            files_meta.append(
                {
                    "file": rel,
                    "status": "read_error",
                    "entry_count": 0,
                    "rejected_count": 0,
                }
            )
            rejected.append({"file": rel, "key": "", "reason": "read_error"})
            continue

        masked = prepare_content(raw)
        for ns in NS_DECL_RE.findall(masked):
            ns_declared.add(ns.strip())
        for m in ADD_NS_RE.finditer(masked):
            ns_declared.add(m.group(1).strip())

        file_entries = 0
        file_rejected = 0
        for im in INNER_ID_RE.finditer(masked):
            full = im.group(1)
            parsed = parse_event_key(full)
            if parsed is None:
                file_rejected += 1
                rejected.append(
                    {
                        "file": rel,
                        "key": full,
                        "reason": "not_namespace_number",
                    }
                )
                continue
            if full in seen_keys:
                continue
            seen_keys.add(full)
            namespace, number = parsed
            entries.append((full, namespace, number, rel))
            file_entries += 1

        status = "ok"
        if file_entries == 0 and file_rejected == 0:
            status = "empty"
        elif file_entries == 0 and file_rejected > 0:
            status = "no_valid_events"
        elif file_rejected > 0:
            status = "ok_with_rejects"

        files_meta.append(
            {
                "file": rel,
                "status": status,
                "entry_count": file_entries,
                "rejected_count": file_rejected,
            }
        )

    return EventsBuildResult(
        entries=entries,
        rejected=rejected,
        files=files_meta,
        generated_at=datetime.now(timezone.utc).isoformat(),
        namespaces_declared=sorted(ns_declared),
    )
