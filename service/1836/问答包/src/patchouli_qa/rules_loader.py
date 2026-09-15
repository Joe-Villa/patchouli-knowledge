"""从 rules/ 加载提示词。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RulesBundle:
    find_system: str
    answer_system: str
    find_user_prefix: str = ""
    loop_nudge: str = ""
    force_submit_nudge: str = ""


def load_rules(rules_dir: Path) -> RulesBundle:
    def read(name: str, default: str = "") -> str:
        p = rules_dir / name
        if not p.is_file():
            return default
        return p.read_text(encoding="utf-8").strip() + "\n"

    find_system = read("find_system.txt")
    answer_system = read("answer_system.txt")
    if not find_system.strip():
        raise FileNotFoundError(f"rules/find_system.txt 缺失或空: {rules_dir}")
    if not answer_system.strip():
        raise FileNotFoundError(f"rules/answer_system.txt 缺失或空: {rules_dir}")
    return RulesBundle(
        find_system=find_system,
        answer_system=answer_system,
        find_user_prefix=read("find_user_prefix.txt"),
        loop_nudge=read("loop_nudge.txt"),
        force_submit_nudge=read("force_submit_nudge.txt"),
    )


def build_find_user_prompt(
    rules: RulesBundle, question: str, *, corpus_note: str | None = None
) -> str:
    parts: list[str] = []
    if rules.find_user_prefix.strip():
        parts.append(rules.find_user_prefix.strip() + "\n")
    else:
        parts.append(
            "请为下列问题查找答用证据，完成后调用 submit_evidence_package。\n"
            "建议先 route_query(question=原问题)，再按返回的 prefer / hints / query_aliases 行动。\n"
        )
    if corpus_note:
        parts.append(f"\n{corpus_note.strip()}\n")
    parts.append(f"\n问题：{question.strip()}\n")
    return "".join(parts)
