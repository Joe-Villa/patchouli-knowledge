"""本地启动：在 service/1939 目录执行 `python -m uvicorn app:app --port 1939`。

国策树总览（专属 TAG / 非专属）+ 点选出图下载。
"""

from __future__ import annotations

import logging

from app import serve


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    serve()


if __name__ == "__main__":
    main()
