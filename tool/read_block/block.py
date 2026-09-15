#!/usr/bin/env python3
"""按 key 提取 Clausewitz 脚本块：path + 起止行 + 原文。

默认只切顶层 ``KEY = { ... }``（含 REPLACE:/INJECT: 前缀）。
``depth=any`` 时在任意括号深度匹配同名块头（用于 named_colors 等嵌套）。

不代替 registry；通常先 registry_lookup 再本工具。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from common_registry import _KEY, _depths_before, prepare_content

DEFAULT_GAME = Path(__file__).resolve().parents[2] / "service" / "1836" / "data" / "game"

# 比 registry 索引略宽：允许 ``key = rgb { ... }`` / ``hsv360 { ... }`` 等类型前缀
_BLOCK_HEADER_RE = re.compile(
    rf"(?m)^[ \t]*(?:(REPLACE_OR_CREATE|REPLACE|INJECT):)?({_KEY})"
    rf"[ \t]*=[ \t]*(?:({_KEY})[ \t]+)?\{{"
)


@dataclass
class BlockHit:
    path: str
    key: str
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int
    operator: str | None
    value_tag: str | None  # e.g. rgb / hsv360；裸 ``= {`` 则为 None
    depth: int
    text: str


def _offset_to_line(text: str, offset: int) -> int:
    """1-based 行号；offset 指向该行内某字符。"""
    if offset < 0:
        return 1
    if offset >= len(text):
        return text.count("\n") + 1
    return text.count("\n", 0, offset) + 1


def find_block_end(masked: str, start_brace: int) -> int:
    """从 ``{`` 配到对应 ``}``；返回闭合括号下标。找不到则 -1。"""
    depth = 0
    for j in range(start_brace, len(masked)):
        ch = masked[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def extract_blocks(
    text: str,
    key: str,
    *,
    depth_mode: str = "top",
) -> list[BlockHit]:
    """在单文件文本中按 key 切块。

    depth_mode:
      - top: 仅深度 0 的块头
      - any: 任意深度块头
    """
    if depth_mode not in {"top", "any"}:
        raise ValueError(f"depth_mode must be top|any, got {depth_mode!r}")

    key = key.strip()
    if not key:
        return []

    masked = prepare_content(text)
    depths = _depths_before(masked)
    hits: list[BlockHit] = []

    for m in _BLOCK_HEADER_RE.finditer(masked):
        found_key = m.group(2)
        if found_key != key:
            continue
        header_depth = depths[m.start()]
        if depth_mode == "top" and header_depth != 0:
            continue

        brace = m.end() - 1
        if masked[brace] != "{":
            continue
        end = find_block_end(masked, brace)
        if end < 0:
            continue

        op = m.group(1)
        value_tag = m.group(3)
        # 块从行首 key 起（m.start），到闭合 } 止
        start_off = m.start()
        end_off = end
        hits.append(
            BlockHit(
                path="",
                key=found_key,
                start_line=_offset_to_line(text, start_off),
                end_line=_offset_to_line(text, end_off),
                start_offset=start_off,
                end_offset=end_off,
                operator=op,
                value_tag=value_tag,
                depth=header_depth,
                text=text[start_off : end_off + 1],
            )
        )

    return hits


def read_block(
    path: Path,
    key: str,
    *,
    depth_mode: str = "top",
    game_root: Path | None = None,
) -> dict:
    """读文件并切块；返回可 JSON 序列化的结果字典。"""
    path = Path(path)
    if not path.is_file():
        # 允许相对 game_root
        if game_root is not None:
            alt = Path(game_root) / path
            if alt.is_file():
                path = alt
            else:
                return {
                    "ok": False,
                    "error": "file_not_found",
                    "path": str(path),
                    "key": key,
                }
        else:
            return {
                "ok": False,
                "error": "file_not_found",
                "path": str(path),
                "key": key,
            }

    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as e:
        return {
            "ok": False,
            "error": "read_error",
            "detail": str(e),
            "path": str(path),
            "key": key,
        }

    rel = str(path)
    if game_root is not None:
        try:
            rel = path.resolve().relative_to(Path(game_root).resolve()).as_posix()
        except ValueError:
            rel = path.as_posix()

    hits = extract_blocks(text, key, depth_mode=depth_mode)
    for h in hits:
        h.path = rel

    if not hits:
        return {
            "ok": False,
            "error": "not_found",
            "path": rel,
            "key": key,
            "depth_mode": depth_mode,
            "hits": [],
        }

    if len(hits) == 1:
        h = hits[0]
        out = asdict(h)
        out["ok"] = True
        out["ambiguous"] = False
        return out

    return {
        "ok": True,
        "ambiguous": True,
        "error": "ambiguous",
        "path": rel,
        "key": key,
        "depth_mode": depth_mode,
        "hits": [asdict(h) for h in hits],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="按 key 提取脚本块")
    parser.add_argument(
        "path",
        type=Path,
        help="文件路径（绝对，或相对 --game）",
    )
    parser.add_argument("key", help="块头 key，如 ideology_despotic_utopian")
    parser.add_argument(
        "--game",
        type=Path,
        default=DEFAULT_GAME,
        help="game 根目录（解析相对 path / 输出相对路径）",
    )
    parser.add_argument(
        "--depth",
        choices=("top", "any"),
        default="top",
        help="top=仅顶层；any=任意嵌套深度",
    )
    parser.add_argument(
        "--no-text",
        action="store_true",
        help="结果中省略 text 字段（只留行号）",
    )
    parser.add_argument("--compact", action="store_true", help="单行 JSON")
    args = parser.parse_args()

    result = read_block(
        args.path,
        args.key,
        depth_mode=args.depth,
        game_root=args.game,
    )
    if args.no_text:
        if "text" in result:
            result.pop("text")
        for h in result.get("hits") or []:
            h.pop("text", None)

    if args.compact:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        sys.exit(1)
    if result.get("ambiguous"):
        sys.exit(2)


if __name__ == "__main__":
    main()
