"""找 → 答 全链路（不含 QQ I/O）。

优先使用请求包::

    from patchouli import RequestConfig, run_request
    run_request(q, RequestConfig.defaults())

本模块保留 CLI / 兼容旧调用。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_FIND_DIR = Path(__file__).resolve().parent
_CORE = _FIND_DIR.parent
_LLM = _CORE / "llm"
_DATA = _CORE / "data"


def _ensure_paths() -> None:
    for p in (_DATA, _LLM, _CORE):
        sp = str(p)
        if sp not in sys.path:
            sys.path.append(sp)
    fd = str(_FIND_DIR)
    if fd in sys.path:
        sys.path.remove(fd)
    sys.path.insert(0, fd)


_ensure_paths()

from .agent import FindResult, find_evidence  # noqa: E402
from answer import AnswerResult, synthesize_answer  # noqa: E402
from client import load_llm_config  # noqa: E402
from reqlog import RequestLogSession  # noqa: E402

log = logging.getLogger("patchouli.find.pipeline")

DEFAULT_GAME = _DATA / "game"
DEFAULT_LOG_DIR = _FIND_DIR / "logs"
DEFAULT_SESSIONS = _CORE / "sandbox" / "sessions"


@dataclass
class PipelineResult:
    ok: bool
    question: str
    answer: str
    find: FindResult
    answer_result: AnswerResult | None
    elapsed_sec: float = 0.0
    log_path: str | None = None
    request_id: str | None = None


def run_pipeline(
    question: str,
    *,
    game_root: Path | None = None,
    max_rounds: int = 12,
    max_wall_sec: float = 180.0,
    log_dir: Path | None = DEFAULT_LOG_DIR,
    skip_answer: bool = False,
    sessions_root: Path | None = DEFAULT_SESSIONS,
) -> PipelineResult:
    t0 = time.monotonic()
    req_log = RequestLogSession.create(log_dir, question=(question or "").strip())

    find_res = find_evidence(
        question,
        game_root=game_root,
        max_rounds=max_rounds,
        max_wall_sec=max_wall_sec,
        log_dir=None,
        sessions_root=sessions_root,
        request_log=req_log,
    )
    ans: AnswerResult | None = None
    answer_text = ""
    if not skip_answer:
        ans = synthesize_answer(
            package=find_res.package,
            log_dir=None,
            request_log=req_log,
        )
        answer_text = ans.answer
    else:
        answer_text = json.dumps(find_res.package.to_dict(), ensure_ascii=False, indent=2)

    elapsed = time.monotonic() - t0
    ok = bool(find_res.ok and (ans.ok if ans else True))
    summary_path = req_log.finalize(ok=ok, answer=answer_text, elapsed_sec=elapsed)

    return PipelineResult(
        ok=ok,
        question=question,
        answer=answer_text,
        find=find_res,
        answer_result=ans,
        elapsed_sec=elapsed,
        log_path=summary_path,
        request_id=req_log.request_id,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="找→答全链路（单问题）")
    parser.add_argument("-q", "--question", required=True)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--max-wall-sec", type=float, default=180.0)
    parser.add_argument("--find-only", action="store_true", help="只找，打印证据包")
    parser.add_argument("--no-log", action="store_true")
    args = parser.parse_args()

    cfg = load_llm_config()
    if not cfg.api_key:
        raise SystemExit("未配置 DEEPSEEK_API_KEY（llm/.env）")

    res = run_pipeline(
        args.question,
        game_root=args.game,
        max_rounds=args.max_rounds,
        max_wall_sec=args.max_wall_sec,
        log_dir=None if args.no_log else DEFAULT_LOG_DIR,
        skip_answer=args.find_only,
    )
    print(res.answer)
    meta = (
        f"[find ok={res.find.ok} stop={res.find.stop_reason} "
        f"rounds={res.find.rounds} tools={res.find.tool_calls} "
        f"cov={res.find.package.coverage} find={res.find.elapsed_sec:.1f}s "
        f"total={res.elapsed_sec:.1f}s]"
    )
    print(meta, file=sys.stderr)
    if res.log_path:
        print(f"[summary] {res.log_path}", file=sys.stderr)
    if not res.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
