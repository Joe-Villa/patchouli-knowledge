"""兼容导出：证据包以 common.agent 为准。"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from common.agent.evidence import EvidenceItem, EvidencePackage, hydrate_package

__all__ = ["EvidenceItem", "EvidencePackage", "hydrate_package"]
