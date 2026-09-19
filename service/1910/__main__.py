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
