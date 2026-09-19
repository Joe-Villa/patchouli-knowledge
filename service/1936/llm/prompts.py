"""作答层 system / user prompt（HOI4）。"""

from __future__ import annotations

from evidence import EvidencePackage

SYSTEM_PROMPT = """你是 Hearts of Iron IV 游戏数据答疑助手（Patchouli / 1936）。
你的唯一依据是用户消息里的「证据包」和「localization 词表」。禁止用常识补全未出现的游戏数值或机制。

证据包由找侧整理：只含回答需要的最终证据（脚本块，和/或 entity_type=computation 的计算结果），并带覆盖度/实体消歧/缺口元数据。
检索过程中的中间跳转块不会出现在包里——不要假设「还有未给出的相关文件」。

输出规则：
1. 只用纯中文叙述；禁止 Markdown：不要用星号、下划线强调、反引号、井号标题、Markdown 链接。
2. 列举用换行、缩进、「一、二、」或「·」即可，不要用「* 」或「- [ ]」。
3. 提到实体时优先用词表或已消歧实体的中文名；必要时可在括号里附 script key / 国家 tag。
4. coverage 为 empty / ambiguous，或缺口标明答不出时：明确说无法确定或存在歧义，不要编造。
5. coverage 为 partial：只根据已有块回答，并可用一两句说明局限。
6. 只回答问题本身，不要复述整段脚本，不要写「根据证据」「作为 AI」之类套话。
7. 优先依据 role=primary 的块组织答案；definition / support 作补充。
8. entity_type=computation（或 path=run_code）的正文是沙箱聚合/计算结果，与脚本块同等权威。
9. 涉及 DLC：若证据里有 has_dlc 条件，说明该内容需对应 DLC；不要编造未出现的 DLC 名。
"""

_COVERAGE_HINT = {
    "sufficient": "找侧认为证据已够回答问题。",
    "partial": "找侧认为证据不完整；请据已有内容答，并点明局限。",
    "empty": "找侧未找到可用脚本块或计算结果；请明确说无法根据资料回答。",
    "ambiguous": "找侧遇到歧义（多实体或多命中）；请说明歧义，不要任选一个假装确定。",
}


def _format_entities(package: EvidencePackage) -> str:
    if not package.resolved_entities:
        return "（无）"
    lines = []
    for e in package.resolved_entities:
        bits = [f"{e.query} → {e.key}"]
        if e.label:
            bits.append(f"「{e.label}」")
        if e.entity_type:
            bits.append(f"type={e.entity_type}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def _format_gaps(items: list, title: str) -> str:
    if not items:
        return ""
    body = "\n".join(f"· {g.format_line()}" for g in items)
    return f"{title}：\n{body}\n\n"


def build_user_prompt(
    question: str,
    package: EvidencePackage,
    glossary_text: str,
    evidence_sections: list[str],
) -> str:
    blocks = "\n\n----\n\n".join(evidence_sections) if evidence_sections else "（无）"
    focus = package.question_focus or "（未标注）"
    cov = package.coverage
    hint = _COVERAGE_HINT.get(cov, "")
    notes = f"找侧备注：{package.notes.strip()}\n\n" if package.notes else ""
    gaps = _format_gaps(package.unresolved, "未解析") + _format_gaps(
        package.not_expanded, "故意未展开"
    )
    if not gaps:
        gaps = "缺口：无\n\n"

    return (
        f"问题：\n{question.strip()}\n\n"
        f"问题焦点（找侧）：{focus}\n"
        f"覆盖度 coverage={cov}。{hint}\n\n"
        f"{notes}"
        f"已消歧实体：\n{_format_entities(package)}\n\n"
        f"{gaps}"
        f"localization 词表（script key = 中文）：\n{glossary_text}\n\n"
        f"答用证据（脚本块和/或计算结果；无检索中间块）：\n{blocks}\n\n"
        "请根据以上内容作答。"
    )
