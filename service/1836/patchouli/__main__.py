"""CLI：python -m patchouli -q '…' [--config path]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import RequestConfig
from .request import run_request


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Patchouli 请求：问题纯文本 → 综述回答（无 QQ）")
    p.add_argument("-q", "--question", required=True)
    p.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="TOML/JSON 配置；默认用核心模块相对路径",
    )
    p.add_argument("--find-only", action="store_true")
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--game", type=Path, default=None)
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--max-wall-sec", type=float, default=None)
    args = p.parse_args(argv)

    cfg = RequestConfig.from_file(args.config) if args.config else RequestConfig.defaults()
    overrides: dict = {}
    if args.find_only:
        overrides["skip_answer"] = True
    if args.no_log:
        overrides["log_dir"] = None
    if args.game is not None:
        overrides["game_root"] = Path(args.game).resolve()
    if args.max_rounds is not None:
        overrides["max_rounds"] = args.max_rounds
    if args.max_wall_sec is not None:
        overrides["max_wall_sec"] = args.max_wall_sec
    if overrides:
        cfg = cfg.with_overrides(**overrides)

    res = run_request(args.question, cfg)
    print(res.answer)
    print(
        f"[ok={res.ok} cov={res.coverage} "
        f"find_rounds={res.find.rounds} total={res.elapsed_sec:.1f}s]",
        file=sys.stderr,
    )
    if res.log_path:
        print(f"[log] {res.log_path}", file=sys.stderr)
    if res.log_dir:
        print(f"[log-dir] {res.log_dir}", file=sys.stderr)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
