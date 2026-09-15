"""找侧 → 答侧的证据包契约。

找侧职责：
- 只放入回答真正需要的最终证据：脚本块，或沙箱计算结果（entity_type=computation）。
- 不塞检索过程中的中间块。
- 附带覆盖度、已消歧实体、缺口等元数据，让答侧几乎只做「读包叙述」。

答侧职责：
- 消费本包；确定性补 loc 词表；单次 LLM 作答。
- 不再根据包外常识补游戏事实。
- computation 正文与脚本块同等权威，可直接据此作答。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Coverage = Literal["sufficient", "partial", "empty", "ambiguous"]
EvidenceRole = Literal["primary", "definition", "support"]

# 沙箱 run_code / 批量统计等派生结果；text 即答用正文（不必再 read_block）
COMPUTATION_ENTITY_TYPE = "computation"

_COVERAGE_OK = frozenset({"sufficient", "partial", "empty", "ambiguous"})
_ROLE_OK = frozenset({"primary", "definition", "support"})


@dataclass
class ResolvedEntity:
    """人话/查询串 → 已消歧的 script key。"""

    query: str
    key: str
    label: str | None = None
    entity_type: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> ResolvedEntity:
        return cls(
            query=str(d.get("query") or ""),
            key=str(d.get("key") or ""),
            label=(str(d["label"]) if d.get("label") is not None else None),
            entity_type=(
                str(d["entity_type"]) if d.get("entity_type") is not None else None
            ),
        )


@dataclass
class GapItem:
    """未解析或故意未展开的项。"""

    reason: str
    key: str | None = None
    query: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> GapItem:
        return cls(
            reason=str(d.get("reason") or ""),
            key=(str(d["key"]) if d.get("key") is not None else None),
            query=(str(d["query"]) if d.get("query") is not None else None),
        )

    def format_line(self) -> str:
        head = self.key or self.query or "（未指名）"
        return f"{head}: {self.reason}"


@dataclass
class EvidenceItem:
    """一条答用证据。

    中间检索跳转用过的块不应出现在此列表。
    脚本块：path + key（系统 hydrate 填 text）。
    计算结果：entity_type=computation，text 直接为 run_code 等输出；path 可用
    ``run_code`` 或数据目录（如 common/history/pops/）。
    """

    path: str
    text: str = ""
    start_line: int | None = None
    end_line: int | None = None
    key: str | None = None
    role: EvidenceRole = "support"
    entity_type: str | None = None
    why: str | None = None
    label: str | None = None  # 主实体显示名（找侧可顺带）
    source: str | None = None  # 对比时的资料库标签（如模组名 / 原版）

    @classmethod
    def from_dict(cls, d: dict) -> EvidenceItem:
        role = str(d.get("role") or "support")
        if role not in _ROLE_OK:
            role = "support"
        return cls(
            path=str(d.get("path") or ""),
            text=str(d.get("text") or ""),
            start_line=d.get("start_line"),
            end_line=d.get("end_line"),
            key=(str(d["key"]) if d.get("key") is not None else None),
            role=role,  # type: ignore[arg-type]
            entity_type=(
                str(d["entity_type"]) if d.get("entity_type") is not None else None
            ),
            why=(str(d["why"]) if d.get("why") is not None else None),
            label=(str(d["label"]) if d.get("label") is not None else None),
            source=(str(d["source"]) if d.get("source") is not None else None),
        )

    def format_section(self) -> str:
        loc = self.path or "（未知路径）"
        if self.start_line is not None and self.end_line is not None:
            loc = f"{loc}:{self.start_line}-{self.end_line}"
        parts = [f"[{loc}]", f"role={self.role}"]
        if self.key:
            parts.append(f"key={self.key}")
        if self.entity_type:
            parts.append(f"type={self.entity_type}")
        if self.label:
            parts.append(f"label={self.label}")
        if self.source:
            parts.append(f"source={self.source}")
        header = " ".join(parts)
        lines = [header]
        if self.why:
            lines.append(f"why: {self.why}")
        lines.append(self.text.strip())
        return "\n".join(lines)


@dataclass
class EvidencePackage:
    """找侧交给答侧的唯一输入结构（外加 question 可在包内或调用参数给）。"""

    coverage: Coverage
    items: list[EvidenceItem] = field(default_factory=list)
    question: str | None = None
    question_focus: str | None = None
    resolved_entities: list[ResolvedEntity] = field(default_factory=list)
    unresolved: list[GapItem] = field(default_factory=list)
    not_expanded: list[GapItem] = field(default_factory=list)
    notes: str | None = None  # 找侧给答侧的短备注；勿塞 CoT

    @classmethod
    def from_dict(cls, d: dict) -> EvidencePackage:
        cov = str(d.get("coverage") or "partial")
        if cov not in _COVERAGE_OK:
            cov = "partial"
        items_raw = d.get("items")
        if items_raw is None and isinstance(d.get("evidence"), list):
            items_raw = d["evidence"]
        items = [
            EvidenceItem.from_dict(x) if isinstance(x, dict) else x
            for x in (items_raw or [])
        ]
        return cls(
            coverage=cov,  # type: ignore[arg-type]
            items=items,
            question=(str(d["question"]) if d.get("question") is not None else None),
            question_focus=(
                str(d["question_focus"]) if d.get("question_focus") is not None else None
            ),
            resolved_entities=[
                ResolvedEntity.from_dict(x) for x in (d.get("resolved_entities") or [])
            ],
            unresolved=[GapItem.from_dict(x) for x in (d.get("unresolved") or [])],
            not_expanded=[GapItem.from_dict(x) for x in (d.get("not_expanded") or [])],
            notes=(str(d["notes"]) if d.get("notes") is not None else None),
        )

    @classmethod
    def from_items(
        cls,
        items: list[EvidenceItem] | list[dict],
        *,
        coverage: Coverage = "sufficient",
        question: str | None = None,
        **kwargs: Any,
    ) -> EvidencePackage:
        """兼容旧 CLI：仅给块列表时包一层。"""
        parsed = [
            e if isinstance(e, EvidenceItem) else EvidenceItem.from_dict(e) for e in items
        ]
        if not parsed:
            coverage = "empty"
        return cls(coverage=coverage, items=parsed, question=question, **kwargs)

    def to_dict(self) -> dict:
        return asdict(self)

    def answer_items(self) -> list[EvidenceItem]:
        """有正文的答用块；已假定找侧未放入中间块。"""
        return [i for i in self.items if i.text.strip()]

    def extra_loc_keys(self) -> list[str]:
        keys: list[str] = []
        for e in self.resolved_entities:
            if e.key:
                keys.append(e.key)
        for i in self.items:
            if i.key:
                keys.append(i.key)
        return keys
