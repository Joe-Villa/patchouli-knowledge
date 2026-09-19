"""LLM 作答层：消费找侧 EvidencePackage → QQ 纯文本回答。"""

from .answer import AnswerResult, synthesize_answer
from .evidence import (
    EvidenceItem,
    EvidencePackage,
    GapItem,
    ResolvedEntity,
)

__all__ = [
    "AnswerResult",
    "EvidenceItem",
    "EvidencePackage",
    "GapItem",
    "ResolvedEntity",
    "synthesize_answer",
]
