"""工坊社区：找侧 → 答侧 一管线。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.answer import synthesize_answer
from agent.evidence import EvidencePackage
from agent.llm import LLMConfig, load_llm_config
from agent.loop import FindResult, find_evidence
from agent.tools import ToolContext
from scoring.fit_tfidf import FitTfidf

log = logging.getLogger("workshop.agent.pipeline")


@dataclass
class AskResult:
    ok: bool
    answer: str
    question: str
    package: EvidencePackage
    find: FindResult | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "answer": self.answer,
            "question": self.question,
            "evidence_summary": {
                "coverage": self.package.coverage,
                "item_count": len(self.package.items),
                "notes": self.package.notes,
                "unresolved": list(self.package.unresolved),
            },
            "items": self.items,
            "find": {
                "rounds": self.find.rounds if self.find else 0,
                "tool_calls": self.find.tool_calls if self.find else 0,
                "elapsed_sec": round(self.find.elapsed_sec, 3) if self.find else 0,
                "stop_reason": self.find.stop_reason if self.find else "",
            },
            "error": self.error,
        }


def _items_from_evidence(package: EvidencePackage, recommend_items: list[dict]) -> list[dict]:
    """优先用 recommend_mods 返回；否则从证据 text 里没法完美还原，仅透传 recommend。"""
    if recommend_items:
        out = []
        for it in recommend_items:
            zh = (it.get("title_zh") or "").strip()
            en = (it.get("title_en") or "").strip()
            title = zh or en or it.get("title") or ""
            out.append(
                {
                    "id": it.get("id"),
                    "modid": it.get("id"),
                    "title": title,
                    "title_zh": zh,
                    "title_en": en,
                    "author": it.get("author") or "",
                    "tags": it.get("tags") or "",
                    "subscribers": it.get("subscriptions") or it.get("subscribers"),
                    "url": it.get("url")
                    or f"https://steamcommunity.com/sharedfiles/filedetails/?id={it.get('id')}",
                    "score": it.get("score"),
                    "fit": it.get("fit"),
                    "hook": "",
                }
            )
        return out
    return []


def invoke(
    question: str,
    *,
    db_path: Path,
    mods: list,
    fit_model: FitTfidf | None,
    memory_prefix: str = "",
    llm_config: LLMConfig | None = None,
) -> AskResult:
    q = (question or "").strip()
    cfg = llm_config or load_llm_config()
    ctx = ToolContext(
        db_path=Path(db_path),
        mods=list(mods or []),
        fit_model=fit_model,
    )
    find = find_evidence(
        q,
        ctx=ctx,
        llm_config=cfg,
        memory_prefix=memory_prefix,
    )
    answer = synthesize_answer(
        q,
        find.package,
        llm_config=cfg,
        memory_prefix=memory_prefix,
    )
    items = _items_from_evidence(find.package, find.recommend_items)
    return AskResult(
        ok=bool(find.ok) and bool(answer),
        answer=answer,
        question=q,
        package=find.package,
        find=find,
        items=items,
        error=find.error,
    )
