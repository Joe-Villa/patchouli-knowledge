#!/usr/bin/env python3
"""根据问题 + 证据包生成 QQ 可读回答。

输入约定（找侧产出）：
- EvidencePackage：coverage / resolved_entities / 答用 items / 缺口元数据
- items 只含回答需要的最终脚本块，不含检索中间块
- 主实体 loc 可放在 item.label 或 resolved_entities；块内引用由本层抽 key 补词表

流程：
1. 解析证据包
2. 从答用块 text 抽 script key → 批量查 localization 词表（确定性）
3. 单次 LLM：问题 + 包元数据 + 证据 + 词表 → 纯文本
4. QQ 格式兜底清洗
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_LLM_DIR = Path(__file__).resolve().parent
if str(_LLM_DIR) not in sys.path:
    sys.path.insert(0, str(_LLM_DIR))

from client import LLMConfig, chat, load_llm_config  # noqa: E402
from evidence import (  # noqa: E402
    Coverage,
    EvidenceItem,
    EvidencePackage,
    ResolvedEntity,
)
from glossary import build_loc_glossary, format_glossary_for_prompt  # noqa: E402
# 强制从本包目录加载 prompts，避免与 find/prompts 冲突
import importlib.util as _ilu

def _load_local(mod_name: str, filename: str):
    path = _LLM_DIR / filename
    spec = _ilu.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(mod_name)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_prompts = _load_local("patchouli_llm_prompts", "prompts.py")
SYSTEM_PROMPT = _prompts.SYSTEM_PROMPT
build_user_prompt = _prompts.build_user_prompt
from qq_text import sanitize_qq_text  # noqa: E402
from read_block import read_block  # noqa: E402

log = logging.getLogger("patchouli.llm.answer")

DEFAULT_GAME = _LLM_DIR  # unused default; callers pass loc_db/game via args
DEFAULT_LOG_DIR = _LLM_DIR / "logs"


@dataclass
class AnswerResult:
    ok: bool
    answer: str
    question: str
    glossary: dict[str, str] = field(default_factory=dict)
    evidence_count: int = 0
    coverage: str | None = None
    elapsed_sec: float = 0.0
    error: str | None = None
    log_path: str | None = None


def synthesize_answer(
    question: str | None = None,
    evidence: list[EvidenceItem] | list[dict] | EvidencePackage | dict | None = None,
    *,
    package: EvidencePackage | dict | None = None,
    lang: str = "simp_chinese",
    loc_db: Path | None = None,
    loc_dbs: list[Path] | None = None,
    llm_config: LLMConfig | None = None,
    log_dir: Path | None = DEFAULT_LOG_DIR,
    extra_loc_keys: list[str] | None = None,
    coverage: Coverage | None = None,
    request_log: Any = None,
    system_prompt: str | None = None,
) -> AnswerResult:
    """作答入口。

    推荐：传入完整 ``package``（或 evidence=EvidencePackage/dict 包）。
    兼容：evidence=块列表时自动包成 EvidencePackage（缺元数据）。
    """
    t0 = time.monotonic()
    pkg = _coerce_package(
        question=question,
        evidence=evidence,
        package=package,
        coverage=coverage,
    )
    q = (pkg.question or question or "").strip()
    if not q:
        raise ValueError("需要 question（参数或 package.question）")

    items = pkg.answer_items()
    texts = [i.text for i in items]
    extras = list(extra_loc_keys or [])
    extras.extend(pkg.extra_loc_keys())

    glossary: dict[str, str] = {}
    db_list = list(loc_dbs) if loc_dbs else ([loc_db] if loc_db is not None else [None])
    if not db_list:
        db_list = [None]
    for db in db_list:
        part = build_loc_glossary(
            texts,
            lang=lang,
            db_path=db,
            extra_keys=extras,
        )
        for k, v in part.items():
            glossary.setdefault(k, v)
    # 找侧已给的 label 并入词表（优先不覆盖库里已有？以库为准；缺则用找侧）
    for e in pkg.resolved_entities:
        if e.key and e.label and e.key not in glossary:
            glossary[e.key] = e.label
    for i in items:
        if i.key and i.label and i.key not in glossary:
            glossary[i.key] = i.label

    glossary_text = format_glossary_for_prompt(glossary)
    sections = [i.format_section() for i in items]
    user_prompt = build_user_prompt(q, pkg, glossary_text, sections)
    sys_content = system_prompt if system_prompt is not None else SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": user_prompt},
    ]

    result = AnswerResult(
        ok=False,
        answer="",
        question=q,
        glossary=glossary,
        evidence_count=len(sections),
        coverage=pkg.coverage,
    )

    cfg = llm_config or load_llm_config()
    llm_request = {
        "model": cfg.model,
        "temperature": 0.2,
        "messages": messages,
    }
    llm_raw: dict | None = None
    t_llm = time.monotonic()
    try:
        chat_res = chat(messages, config=cfg)
        llm_raw = chat_res.raw
        raw_text = (chat_res.message.content or "").strip()
        result.ok = True
        result.answer = sanitize_qq_text(raw_text)
    except Exception as exc:
        log.exception("synthesize_answer failed: %s", exc)
        result.error = f"{type(exc).__name__}: {exc}"
        result.answer = f"[作答失败: {result.error}]"

    llm_elapsed = time.monotonic() - t_llm
    result.elapsed_sec = time.monotonic() - t0

    llm_call_entry = {
        "phase": "answer",
        "elapsed_sec": llm_elapsed,
        "error": result.error,
        "request": llm_request,
        "response": llm_raw,
        "answer_text": result.answer,
    }
    if request_log is not None:
        llm_call_entry = request_log.record_answer_llm_call(
            request=llm_request,
            response=llm_raw,
            elapsed_sec=llm_elapsed,
            error=result.error,
            answer_text=result.answer,
        )

    detail = {
        "question": q,
        "ok": result.ok,
        "error": result.error,
        "elapsed_sec": result.elapsed_sec,
        "coverage": pkg.coverage,
        "evidence_count": len(sections),
        "package": pkg.to_dict(),
        "glossary": glossary,
        "messages": messages,
        "answer": result.answer,
        "llm_call": llm_call_entry,
    }

    if request_log is not None:
        try:
            result.log_path = request_log.write_answer_detail(detail)
        except OSError as e:
            log.warning("write request_log answer failed: %s", e)

    if log_dir is not None:
        try:
            legacy = _write_log(detail, Path(log_dir))
            if result.log_path is None:
                result.log_path = str(legacy)
        except OSError as e:
            log.warning("write log failed: %s", e)

    return result


def _coerce_package(
    *,
    question: str | None,
    evidence: list[EvidenceItem] | list[dict] | EvidencePackage | dict | None,
    package: EvidencePackage | dict | None,
    coverage: Coverage | None,
) -> EvidencePackage:
    if package is not None:
        pkg = (
            package
            if isinstance(package, EvidencePackage)
            else EvidencePackage.from_dict(package)
        )
        if question and not pkg.question:
            pkg.question = question
        if coverage is not None:
            pkg.coverage = coverage
        return pkg

    if isinstance(evidence, EvidencePackage):
        if question and not evidence.question:
            evidence.question = question
        if coverage is not None:
            evidence.coverage = coverage
        return evidence

    if isinstance(evidence, dict) and (
        "coverage" in evidence or "items" in evidence or "resolved_entities" in evidence
    ):
        pkg = EvidencePackage.from_dict(evidence)
        if question and not pkg.question:
            pkg.question = question
        if coverage is not None:
            pkg.coverage = coverage
        return pkg

    items: list[EvidenceItem] | list[dict] = evidence if isinstance(evidence, list) else []
    return EvidencePackage.from_items(
        items,
        coverage=coverage or ("sufficient" if items else "empty"),
        question=question,
    )


def _write_log(payload: dict, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"answer_{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _load_item_from_span(s: dict, *, game: Path) -> EvidenceItem:
    """span: 已含 text，或 path+key 现场 read_block；可带 role/why 等元数据。"""
    meta = {k: s.get(k) for k in ("role", "entity_type", "why", "label") if k in s}
    if s.get("text"):
        return EvidenceItem.from_dict({**s, **meta})
    path = s.get("path")
    key = s.get("key")
    if not path or not key:
        raise ValueError(f"span 需要 path+key 或 text: {s!r}")
    block = read_block(path, key, game_root=game)
    if not block.get("ok"):
        raise RuntimeError(f"read_block failed: {block}")
    if block.get("ambiguous"):
        hit = block["hits"][0]
        return EvidenceItem.from_dict(
            {
                "path": hit.get("path") or path,
                "start_line": hit.get("start_line"),
                "end_line": hit.get("end_line"),
                "text": hit.get("text") or "",
                "key": key,
                **meta,
            }
        )
    return EvidenceItem.from_dict(
        {
            "path": block.get("path") or path,
            "start_line": block.get("start_line"),
            "end_line": block.get("end_line"),
            "text": block.get("text") or "",
            "key": key,
            **meta,
        }
    )


def _hydrate_package(data: dict, *, game: Path) -> EvidencePackage:
    """包 JSON：可缺 text，用 path+key 填充；保留元数据。"""
    raw_items = data.get("items")
    if raw_items is None and isinstance(data.get("evidence"), list):
        raw_items = data["evidence"]
        data = {**data, "items": raw_items}
    if not isinstance(raw_items, list):
        raise ValueError("package 需要 items 数组")
    hydrated = [_load_item_from_span(x, game=game) for x in raw_items]
    pkg = EvidencePackage.from_dict({**data, "items": [asdict(i) for i in hydrated]})
    return pkg


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="证据包 → QQ 纯文本回答")
    parser.add_argument("--question", "-q", default=None, help="可省略若 package 含 question")
    parser.add_argument(
        "--package-json",
        type=Path,
        help="完整证据包 JSON（推荐）：含 coverage / items / resolved_entities / …",
    )
    parser.add_argument(
        "--evidence-json",
        type=Path,
        help="兼容：块数组，或完整包（有 coverage/items 时按包解析）",
    )
    parser.add_argument(
        "--span",
        action="append",
        default=[],
        metavar="PATH::KEY",
        help="快捷：读一块作 primary。例 common/ideologies/…::ideology_despotic_utopian",
    )
    parser.add_argument(
        "--role",
        default="primary",
        choices=["primary", "definition", "support"],
        help="--span 的默认 role",
    )
    parser.add_argument("--coverage", default=None, choices=sorted(["sufficient", "partial", "empty", "ambiguous"]))
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--lang", default="simp_chinese")
    parser.add_argument("--loc-db", type=Path, default=None)
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument("--dump-glossary", action="store_true")
    args = parser.parse_args()

    pkg: EvidencePackage | None = None

    if args.package_json:
        data = json.loads(args.package_json.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit("--package-json 须为对象")
        pkg = _hydrate_package(data, game=args.game)

    if args.evidence_json:
        data = json.loads(args.evidence_json.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            pkg = _hydrate_package(data, game=args.game)
        elif isinstance(data, list):
            items = [_load_item_from_span(x, game=args.game) for x in data]
            pkg = EvidencePackage.from_items(
                items,
                coverage=args.coverage or "sufficient",
                question=args.question,
            )
        else:
            raise SystemExit("evidence-json 须为对象（包）或数组（块列表）")

    span_items: list[EvidenceItem] = []
    for spec in args.span:
        if "::" not in spec:
            raise SystemExit(f"--span 格式 PATH::KEY，收到: {spec}")
        path, key = spec.split("::", 1)
        span_items.append(
            _load_item_from_span(
                {"path": path, "key": key, "role": args.role},
                game=args.game,
            )
        )

    if span_items:
        if pkg is None:
            pkg = EvidencePackage.from_items(
                span_items,
                coverage=args.coverage or "sufficient",
                question=args.question,
            )
        else:
            pkg.items.extend(span_items)

    if pkg is None:
        raise SystemExit("需要 --package-json / --evidence-json / --span")

    if args.question:
        pkg.question = args.question
    if args.coverage:
        pkg.coverage = args.coverage  # type: ignore[assignment]

    # --span 快捷：补一条消歧实体
    if span_items and not pkg.resolved_entities:
        for it in span_items:
            if it.key:
                pkg.resolved_entities.append(
                    ResolvedEntity(query=it.key, key=it.key, entity_type=it.entity_type)
                )

    result = synthesize_answer(
        package=pkg,
        lang=args.lang,
        loc_db=args.loc_db,
        log_dir=None if args.no_log else DEFAULT_LOG_DIR,
    )
    if args.dump_glossary:
        print("## glossary")
        for k, v in sorted(result.glossary.items()):
            print(f"{k}\t{v}")
        print("## answer")
    print(result.answer)
    if result.log_path:
        print(f"\n[log] {result.log_path}", file=sys.stderr)
    if not result.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
