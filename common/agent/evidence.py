"""证据包：找侧提交 → 答侧输入（通用最小契约）。

服务可在 hydrate 时填入更丰富字段；壳只要求 coverage/items/notes/unresolved。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EvidenceItem:
    path: str = ""
    key: str = ""
    entity_type: str = ""
    text: str = ""
    note: str = ""
    role: str = ""
    # 扩展：服务可塞入任意额外键到 to_dict via extras
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "path": self.path,
            "key": self.key,
            "entity_type": self.entity_type,
            "text": self.text,
            "note": self.note,
        }
        if self.role:
            d["role"] = self.role
        if self.extras:
            d.update(self.extras)
        return d


@dataclass
class EvidencePackage:
    coverage: str = "empty"  # sufficient | partial | empty | ambiguous
    items: list[EvidenceItem] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    notes: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "coverage": self.coverage,
            "items": [i.to_dict() for i in self.items],
            "unresolved": list(self.unresolved),
            "notes": self.notes,
        }
        if self.extras:
            d.update(self.extras)
        return d

    def to_prompt_block(self) -> str:
        lines = [f"coverage: {self.coverage}", f"notes: {self.notes or '（无）'}", "items:"]
        if not self.items:
            lines.append("  （空）")
        for i, it in enumerate(self.items, 1):
            lines.append(
                f"  {i}. type={it.entity_type or '-'} path={it.path or '-'} "
                f"key={it.key or '-'}\n     {it.text or it.note or ''}"
            )
        if self.unresolved:
            lines.append("unresolved: " + "; ".join(str(x) for x in self.unresolved))
        return "\n".join(lines)


def hydrate_package(obj: dict[str, Any] | None) -> EvidencePackage:
    """通用 hydrate：接受精简 submit JSON。"""
    if not isinstance(obj, dict):
        return EvidencePackage(coverage="empty", notes="invalid submit")
    cov = str(obj.get("coverage") or "partial").strip().lower()
    if cov not in ("sufficient", "partial", "empty", "ambiguous"):
        cov = "partial"
    items: list[EvidenceItem] = []
    for raw in obj.get("items") or []:
        if not isinstance(raw, dict):
            continue
        known = {"path", "key", "entity_type", "text", "note", "role", "excerpt"}
        extras = {k: v for k, v in raw.items() if k not in known}
        items.append(
            EvidenceItem(
                path=str(raw.get("path") or ""),
                key=str(raw.get("key") or ""),
                entity_type=str(raw.get("entity_type") or ""),
                text=str(raw.get("text") or raw.get("excerpt") or ""),
                note=str(raw.get("note") or ""),
                role=str(raw.get("role") or ""),
                extras=extras,
            )
        )
    unresolved_raw = obj.get("unresolved") or []
    unresolved: list[str] = []
    for x in unresolved_raw:
        if isinstance(x, dict):
            unresolved.append(str(x.get("reason") or x.get("query") or x))
        else:
            unresolved.append(str(x))
    skip = {"coverage", "items", "unresolved", "notes"}
    extras = {k: v for k, v in obj.items() if k not in skip}
    return EvidencePackage(
        coverage=cov,
        items=items,
        unresolved=unresolved,
        notes=str(obj.get("notes") or ""),
        extras=extras,
    )
