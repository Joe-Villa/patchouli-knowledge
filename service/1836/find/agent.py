"""1836 找侧：启用 common.agent 实例（Vic3 脚本语料）。"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_FIND_DIR = Path(__file__).resolve().parent
_CORE = _FIND_DIR.parent
_DATA = _CORE / "data"
_LLM = _CORE / "llm"
_SANDBOX = _CORE / "sandbox"
_REPO = _CORE.parent.parent  # service/1836 → service → repo


def _ensure_paths() -> None:
    for p in (_DATA, _LLM, _CORE, str(_REPO)):
        sp = str(p)
        if sp not in sys.path:
            sys.path.append(sp)
    fd = str(_FIND_DIR)
    if fd in sys.path:
        sys.path.remove(fd)
    sys.path.insert(0, fd)
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))


_ensure_paths()

from client import LLMConfig, load_llm_config  # noqa: E402
from evidence import EvidencePackage, GapItem  # noqa: E402
from sandbox import SandboxSession  # noqa: E402

from common.agent import AgentInstance, run_find  # noqa: E402
from find_prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from tools import (  # noqa: E402
    TOOL_SCHEMAS,
    ToolContext,
    dumps_tool_result,
    make_dispatcher,
)
from agent_hydrate import _hydrate_submit, _write_log  # noqa: E402

log = logging.getLogger("patchouli.find.agent")

ProgressCallback = Callable[[dict[str, Any]], None]

DEFAULT_GAME = _DATA / "game"
DEFAULT_LOG_DIR = _FIND_DIR / "logs"
DEFAULT_MAX_ROUNDS = 12
DEFAULT_SUBMIT_GRACE = 3
DEFAULT_MAX_WALL_SEC = 180.0


@dataclass
class FindResult:
    ok: bool
    package: EvidencePackage
    question: str
    rounds: int = 0
    tool_calls: int = 0
    elapsed_sec: float = 0.0
    stop_reason: str = ""
    session_id: str | None = None
    log_path: str | None = None
    error: str | None = None
    transcript: list[dict] = field(default_factory=list)


def find_evidence(
    question: str,
    *,
    game_root: Path | None = None,
    loc_db: Path | None = None,
    llm_config: LLMConfig | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    submit_grace: int = DEFAULT_SUBMIT_GRACE,
    max_wall_sec: float = DEFAULT_MAX_WALL_SEC,
    log_dir: Path | None = DEFAULT_LOG_DIR,
    lang: str = "simp_chinese",
    sessions_root: Path | None = None,
    request_log: Any = None,
    corpus_note: str | None = None,
    on_progress: ProgressCallback | None = None,
    progress_side_index: int = 0,
    progress_side_count: int = 1,
    progress_side_label: str | None = None,
    mods_roots: dict[str, Path] | None = None,
    visible_mod_ids: frozenset[str] | None = None,
) -> FindResult:
    q = (question or "").strip()
    if not q:
        raise ValueError("question 为空")

    game = Path(game_root or DEFAULT_GAME).resolve()
    loc = Path(loc_db or (game / "localization.sqlite"))
    t0 = time.monotonic()
    session = SandboxSession.create(
        game_root=game,
        sessions_root=sessions_root,
        mods_roots=mods_roots,
    )
    visible = frozenset(visible_mod_ids or (mods_roots or {}).keys())
    ctx = ToolContext(
        game_root=game,
        loc_db=loc,
        sandbox=session,
        lang=lang,
        mods_root=session.mods_view,
        visible_mod_ids=visible,
    )
    dispatch_map = make_dispatcher(ctx)

    def _dispatch(name: str, args: dict[str, Any]) -> Any:
        fn = dispatch_map.get(name)
        if fn is None:
            return {"ok": False, "error": f"unknown_tool:{name}"}
        return fn(args)

    def _hydrate(args: dict[str, Any]) -> EvidencePackage:
        return _hydrate_submit(
            args,
            question=q,
            game=game,
            mods_root=session.mods_view,
            visible_mod_ids=visible,
        )

    inst = AgentInstance(
        name="1836-vic3",
        system_prompt=SYSTEM_PROMPT,
        tool_schemas=TOOL_SCHEMAS,
        dispatch=_dispatch,
        hydrate_submit=_hydrate,
        dumps_tool_result=dumps_tool_result,
        max_rounds=max_rounds,
        submit_grace=submit_grace,
        max_wall_sec=max_wall_sec,
        temperature=0.15,
        reject_non_submit_when_forced=True,
        nudge_when_no_tool=True,
    )

    def _progress(ev: dict[str, Any]) -> None:
        if on_progress is None:
            return
        try:
            on_progress(
                {
                    **ev,
                    "side_index": progress_side_index,
                    "side_count": progress_side_count,
                    "side_label": progress_side_label,
                }
            )
        except Exception:
            log.debug("on_progress failed", exc_info=True)

    try:
        raw = run_find(
            inst,
            q,
            user_content=build_user_prompt(q, corpus_note=corpus_note),
            llm_config=llm_config or load_llm_config(),
            on_progress=_progress,
        )
        pkg = raw.package
        if not isinstance(pkg, EvidencePackage):
            # common package → 尽量包一层
            pkg = EvidencePackage(
                coverage=getattr(pkg, "coverage", "empty") or "empty",
                notes=str(getattr(pkg, "notes", "") or ""),
                question=q,
                unresolved=[
                    GapItem(reason=str(x), query=q)
                    for x in (getattr(pkg, "unresolved", None) or [])
                ],
            )

        result_obj = FindResult(
            ok=bool(raw.ok),
            package=pkg,
            question=q,
            rounds=raw.rounds,
            tool_calls=raw.tool_calls,
            elapsed_sec=raw.elapsed_sec,
            stop_reason=raw.stop_reason,
            session_id=session.session_id,
            error=raw.error,
            transcript=list(raw.transcript),
        )

        if log_dir is not None or request_log is not None:
            detail = {
                "question": q,
                "ok": result_obj.ok,
                "stop_reason": raw.stop_reason,
                "error": raw.error,
                "elapsed_sec": raw.elapsed_sec,
                "rounds": result_obj.rounds,
                "tool_calls": raw.tool_calls,
                "session_id": session.session_id,
                "max_rounds": max_rounds,
                "submit_grace": submit_grace,
                "package": pkg.to_dict() if hasattr(pkg, "to_dict") else {},
                "transcript": raw.transcript,
                "final_messages": raw.messages,
            }
            if request_log is not None:
                try:
                    request_log.write_find_detail(detail)
                except Exception:
                    log.exception("request_log write_find_detail failed")
            if log_dir is not None:
                try:
                    result_obj.log_path = str(_write_log(detail, Path(log_dir)))
                except Exception:
                    log.exception("write find log failed")

        return result_obj
    finally:
        try:
            session.close()
        except Exception:
            log.debug("sandbox close failed", exc_info=True)
        _ = t0
