"""CLI: python -m patchouli_qa -q '…' --root …"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PatchouliQA invoke")
    p.add_argument("--root", type=Path, required=True, help="实例 root_path")
    p.add_argument("-q", "--question", required=True)
    p.add_argument(
        "--constraint",
        action="append",
        default=[],
        help="data_constraint 语料名，可重复；默认 vanilla",
    )
    p.add_argument("--log-dir", type=Path, default=None, help="output_log 目录")
    p.add_argument("--skip-answer", action="store_true")
    p.add_argument("--json", action="store_true", help="打印 InvokeResult 摘要 JSON")
    args = p.parse_args(argv)

    # 允许未 pip install：把 src 加进 path
    src = Path(__file__).resolve().parents[2] / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from patchouli_qa import PatchouliQA

    constraints = args.constraint or ["vanilla"]
    log_dir = args.log_dir or (args.root / "logs")
    qa = PatchouliQA.init(args.root)
    r = qa.invoke(
        args.question,
        constraints,
        log_dir,
        skip_answer=args.skip_answer or None,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "ok": r.ok,
                    "coverage": r.coverage,
                    "stop_reason": r.stop_reason,
                    "elapsed_sec": r.elapsed_sec,
                    "log_dir": r.log_dir,
                    "answer": r.answer,
                    "error": r.error,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(r.answer)
        print(f"\n--- ok={r.ok} coverage={r.coverage} log={r.log_dir}", file=sys.stderr)
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
