"""python -m web — 从 service/1836 根目录启动（PYTHONPATH 含本目录与仓库根）。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def _bootstrap_paths() -> None:
    web_dir = Path(__file__).resolve().parent
    core = web_dir.parent  # service/1836
    repo = core.parent.parent
    for p in (core, repo, web_dir.parent):  # core twice-safe; ensure core first
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    # Make `import web` resolve to this package when cwd varies
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))


def main() -> None:
    _bootstrap_paths()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from web.app import serve

    serve()


if __name__ == "__main__":
    main()
