#!/usr/bin/env python3
"""为惊喜短名单 Top400 批量生成中文简介，写出 Markdown。

复用 service.present 的截断/调用方式；目录场景禁止 refuse，每条都必须有 hook。
断点：out/surprise_intros.jsonl（按 modid 覆盖）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from scoring.catalog import load_ge300_details, resolve_brief_path  # noqa: E402
from scoring.surprise import DEFAULT_SHORTLIST_K, build_surprise_shortlist  # noqa: E402
from service.present import (  # noqa: E402
    CANDIDATE_N,
    _chat_json,
    _truncate_desc,
)
from service.rewrite import load_rewrite_config  # noqa: E402

OUT_DIR = ROOT / "out"
CHECKPOINT = OUT_DIR / "surprise_intros.jsonl"
# 给人看的短名单落在 file/1837/out；断点 jsonl 仍留 service 侧
MARKDOWN = ROOT.parent.parent / "file" / "1837" / "out" / "惊喜短名单400.md"

_SYSTEM = """你是维多利亚3创意工坊模组推荐的「可玩点」编辑。

你会收到若干候选模组（标题、作者、标签、订阅、简介摘录）。
简介来自工坊真实数据，请据此写，禁止编造简介里没有的机制。

对每个候选必须 keep，并写 1～2 句中文「可玩点」简介：
- 要具体、能勾起兴趣，强调好玩/特别之处（惊喜发现口径）
- 简介偏薄时据已有信息诚实写，可点明信息有限；仍禁止捏造机制
- 不要夸大订阅；不要提「AI/模型」
- 不要 refuse

只输出 JSON：
{
  "items": [
    {"id":"<与输入相同的模组id>","action":"keep","hook":"..."}
  ]
}
每个输入模组都必须出现恰好一次；不要编造 id。"""

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))


def display_title(c: dict) -> str:
    zh = (c.get("title_zh") or "").strip()
    en = (c.get("title_en") or "").strip()
    title = (c.get("title") or "").strip()
    if _has_cjk(zh):
        return zh
    if _has_cjk(title) and not _has_cjk(en):
        return title
    return en or zh or title or str(c.get("id") or "")


def mod_to_candidate(ranked) -> dict:
    m = ranked.mod
    return {
        "id": m.id,
        "title": m.title or "",
        "title_zh": (m.title_zh or "").strip(),
        "title_en": (m.title_en or "").strip(),
        "author": m.author or "",
        "tags": m.tags or "",
        "subscribers": m.subscribers,
        "description": m.description or "",
        "rank": ranked.rank,
        "score": round(ranked.score, 3),
    }


def load_checkpoint(path: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    if not path.is_file():
        return by_id
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = str(obj.get("id") or "").strip()
        if mid and (obj.get("hook") or "").strip():
            by_id[mid] = obj
    return by_id


def append_checkpoint(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_user_blob(candidates: list[dict]) -> str:
    lines = [
        "模式：惊喜发现（为短名单写可玩点简介，全部保留）。",
        "用户原问：给我惊喜",
        "",
        "候选模组：",
    ]
    for i, c in enumerate(candidates, start=1):
        mid = str(c.get("id") or "")
        author = (c.get("author") or "").strip() or "（未知）"
        title_zh = (c.get("title_zh") or "").strip()
        title_en = (c.get("title_en") or "").strip()
        title = (c.get("title") or "").strip()
        if title_zh or title_en:
            title_line = f"中文标题：{title_zh or title}｜英文标题：{title_en or title}"
        else:
            title_line = f"标题：{title}"
        lines.append(
            f"{i}. id={mid}\n"
            f"   {title_line}\n"
            f"   作者：{author}\n"
            f"   标签：{c.get('tags') or '（无）'}\n"
            f"   订阅：{c.get('subscribers', 0)}\n"
            f"   简介：{_truncate_desc(str(c.get('description') or ''))}"
        )
    return "\n".join(lines)


def fallback_hook(c: dict) -> str:
    desc = _truncate_desc(str(c.get("description") or ""), 160)
    hook = desc.split("。")[0].strip() or desc[:80]
    if not hook:
        return "工坊简介有限，可点开详情自行判断。"
    if not _has_cjk(hook):
        return f"工坊简介要点：{hook}"
    return hook


def present_batch(candidates: list[dict], *, config, retries: int = 3) -> dict[str, str]:
    """返回 id -> hook。"""
    user_blob = build_user_blob(candidates)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            obj = _chat_json(
                system=_SYSTEM,
                user=user_blob,
                config=config,
                temperature=0.35,
            )
            by_id: dict[str, str] = {}
            for raw in obj.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                mid = str(raw.get("id") or "").strip()
                hook = str(raw.get("hook") or "").strip()
                if mid and hook:
                    by_id[mid] = hook
            # 缺的补 fallback，保证本批齐全
            for c in candidates:
                mid = str(c["id"])
                if mid not in by_id:
                    by_id[mid] = fallback_hook(c)
            return by_id
        except Exception as e:
            last_err = e
            time.sleep(min(2**attempt, 20))
    print(f"  batch failed after retries: {last_err}; using fallback", flush=True)
    return {str(c["id"]): fallback_hook(c) for c in candidates}


def write_markdown(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 惊喜短名单（Interesting Top 400）",
        "",
        f"共 {len(rows)} 个模组。标题优先中文，无中文则英文；含订阅量与作者；简介由 DeepSeek 按可玩点管线生成。",
        "",
    ]
    for i, row in enumerate(rows, start=1):
        title = row["title"]
        mid = row["id"]
        hook = row["hook"]
        author = (row.get("author") or "").strip() or "（未知）"
        try:
            subs = int(row.get("subscribers") or 0)
        except (TypeError, ValueError):
            subs = 0
        lines.append(f"## {i}. {title}")
        lines.append("")
        lines.append(f"- modid: `{mid}`")
        lines.append(f"- 作者：{author}")
        lines.append(f"- 订阅量：{subs}")
        lines.append(f"- 简介：{hook}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=DEFAULT_SHORTLIST_K)
    ap.add_argument("--batch-size", type=int, default=CANDIDATE_N)
    ap.add_argument("--sleep", type=float, default=0.4, help="批次间隔秒")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 条（调试）")
    ap.add_argument("--force", action="store_true", help="忽略断点重跑全部")
    ap.add_argument(
        "--markdown-only",
        action="store_true",
        help="不调 LLM，仅用断点+短名单重写 Markdown",
    )
    ap.add_argument(
        "--db",
        default=str(REPO / "核心模块/data/workshop_details/workshop_details_all.sqlite"),
    )
    ap.add_argument(
        "--brief",
        default=str(REPO / "核心模块/data/workshop_brief/vic3_mods_all.sqlite"),
    )
    ap.add_argument("--checkpoint", default=str(CHECKPOINT))
    ap.add_argument("--markdown", default=str(MARKDOWN))
    args = ap.parse_args()

    db = Path(args.db)
    brief = resolve_brief_path(args.brief)
    print(f"load db={db} brief={brief}", flush=True)
    mods = load_ge300_details(db, brief_path=brief)
    shortlist = build_surprise_shortlist(mods, top_k=max(1, args.top_k))
    candidates = [mod_to_candidate(r) for r in shortlist]
    if args.limit > 0:
        candidates = candidates[: args.limit]

    ck_path = Path(args.checkpoint)
    md_path = Path(args.markdown)
    done = {} if args.force else load_checkpoint(ck_path)

    if not args.markdown_only:
        cfg = load_rewrite_config()
        if not cfg.api_key:
            print("缺少 DEEPSEEK_API_KEY（检查 模组推荐/.env）", file=sys.stderr)
            return 1
        print(
            f"shortlist={len(candidates)} batch={args.batch_size} model={cfg.model}",
            flush=True,
        )
        if args.force and ck_path.exists():
            trash = (
                Path.home()
                / ".local/share/Trash/files"
                / f"surprise_intros_{int(time.time())}.jsonl"
            )
            trash.parent.mkdir(parents=True, exist_ok=True)
            ck_path.replace(trash)
            print(f"old checkpoint -> {trash}", flush=True)
            done = {}

        pending = [c for c in candidates if str(c["id"]) not in done]
        print(f"already={len(done)} pending={len(pending)}", flush=True)

        batch_size = max(1, min(args.batch_size, 20))
        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            print(
                f"batch {i // batch_size + 1}/{(len(pending) + batch_size - 1) // batch_size} "
                f"size={len(batch)} ids={batch[0]['id']}..{batch[-1]['id']}",
                flush=True,
            )
            hooks = present_batch(batch, config=cfg)
            for c in batch:
                mid = str(c["id"])
                row = {
                    "id": mid,
                    "title": display_title(c),
                    "title_zh": c.get("title_zh") or "",
                    "title_en": c.get("title_en") or "",
                    "author": c.get("author") or "",
                    "subscribers": int(c.get("subscribers") or 0),
                    "rank": c.get("rank"),
                    "score": c.get("score"),
                    "hook": hooks.get(mid) or fallback_hook(c),
                }
                done[mid] = row
                append_checkpoint(ck_path, row)
            if args.sleep > 0 and i + batch_size < len(pending):
                time.sleep(args.sleep)
    else:
        print(f"markdown-only shortlist={len(candidates)} checkpoint={len(done)}", flush=True)

    # 按短名单顺序写 markdown；作者/订阅以当前库为准
    rows = []
    for c in candidates:
        mid = str(c["id"])
        row = done.get(mid)
        if not row:
            row = {
                "id": mid,
                "title": display_title(c),
                "author": c.get("author") or "",
                "subscribers": int(c.get("subscribers") or 0),
                "hook": fallback_hook(c),
            }
        else:
            row = dict(row)
            row["title"] = display_title(c)
            row["author"] = c.get("author") or row.get("author") or ""
            row["subscribers"] = int(c.get("subscribers") or row.get("subscribers") or 0)
        rows.append(row)

    write_markdown(rows, md_path)
    print(f"wrote {md_path} ({len(rows)} mods)", flush=True)
    print(f"checkpoint {ck_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
