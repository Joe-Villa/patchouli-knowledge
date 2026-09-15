"""候选模组展示层：结合用户原问，为每个模组总结可玩点；可逐个 refuse。"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from scoring.catalog import clean_description
from service.rewrite import RewriteConfig, load_rewrite_config

log = logging.getLogger("recommend.present")

CANDIDATE_N = 15  # 交给 LLM 的上限
DISPLAY_N = 10  # 最终展示上限
_DESC_MAX = 900  # 每条简介送入 LLM 的上限（剥 BBCode 后）

_SYSTEM = """你是维多利亚3创意工坊模组推荐的「可玩点」编辑。

你会收到：用户原问题（或惊喜发现意图），以及若干候选模组（标题、作者、标签、订阅、简介摘录）。
简介来自工坊真实数据，请据此写，禁止编造简介里没有的机制。

对每个候选决定 keep 或 refuse：
- keep：写 1～2 句中文「可玩点」，要具体、能勾起兴趣；尽量回应用户原问（惊喜模式则强调好玩/特别之处）
- refuse：简介空洞、与用户需求明显无关、纯修复/纯翻译且用户没要这类、或无法从简介看出可玩内容

规则：
- 不要夸大订阅；不要提「AI/模型」
- 不要输出 markdown 列表符号以外的格式要求以外的内容
- 只输出 JSON

输出：
{
  "items": [
    {"id":"<与输入相同的模组id>","action":"keep","hook":"..."},
    {"id":"...","action":"refuse","reason":"..."}
  ]
}
每个输入模组都必须出现恰好一次；不要编造 id。"""


@dataclass
class PresentedMod:
    id: str
    action: str  # keep | refuse
    hook: str = ""
    reason: str = ""


@dataclass
class PresentResult:
    items: list[PresentedMod]
    used_llm: bool
    error: str | None = None


def _truncate_desc(text: str, limit: int = _DESC_MAX) -> str:
    s = clean_description(text or "")
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _extract_json(text: str) -> dict:
    s = (text or "").strip()
    if not s:
        raise ValueError("empty")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL | re.IGNORECASE)
    if m:
        s = m.group(1)
    else:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i : j + 1]
    return json.loads(s)


def _chat_json(
    *,
    system: str,
    user: str,
    config: RewriteConfig,
    temperature: float = 0.4,
) -> dict:
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if e.code == 400 and "response_format" in detail:
            payload.pop("response_format", None)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{config.base_url}/chat/completions",
                data=data,
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        else:
            raise RuntimeError(f"DeepSeek HTTP {e.code}: {detail[:300]}") from e

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("empty deepseek response")
    content = (choices[0].get("message") or {}).get("content") or ""
    return _extract_json(content)


def _fallback_keep_all(candidates: list[dict]) -> PresentResult:
    """无密钥或 LLM 失败：全部 keep，hook 用简介首句凑数。"""
    out: list[PresentedMod] = []
    for c in candidates:
        desc = _truncate_desc(str(c.get("description") or ""), 160)
        hook = desc.split("。")[0].strip() or desc[:80] or "工坊简介有限，可点开详情自行判断。"
        out.append(PresentedMod(id=str(c["id"]), action="keep", hook=hook))
    return PresentResult(items=out, used_llm=False)


def present_candidates(
    *,
    user_query: str,
    candidates: list[dict],
    mode: str = "recommend",
    config: RewriteConfig | None = None,
) -> PresentResult:
    """对候选做 keep/refuse + 可玩点。candidates 项需含 id/title/tags/subscribers/description（author 可选）。"""
    if not candidates:
        return PresentResult(items=[], used_llm=False)

    cfg = config or load_rewrite_config()
    if not cfg.api_key:
        return _fallback_keep_all(candidates)

    intent = (user_query or "").strip() or ("给我惊喜" if mode == "surprise" else "")
    mode_line = (
        "模式：惊喜发现（用户没有具体筛选词，挑选值得一试、有钩子的模组）。"
        if mode == "surprise"
        else "模式：按需推荐（可玩点要尽量贴合用户原问）。"
    )
    lines = [
        mode_line,
        f"用户原问：{intent}",
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
    user_blob = "\n".join(lines)

    try:
        obj = _chat_json(system=_SYSTEM, user=user_blob, config=cfg, temperature=0.35)
    except Exception as e:
        log.warning("present LLM failed, fallback keep-all: %s", e)
        r = _fallback_keep_all(candidates)
        return PresentResult(items=r.items, used_llm=False, error=str(e))

    by_id: dict[str, PresentedMod] = {}
    for raw in obj.get("items") or []:
        if not isinstance(raw, dict):
            continue
        mid = str(raw.get("id") or "").strip()
        if not mid:
            continue
        action = str(raw.get("action") or "").strip().lower()
        if action not in {"keep", "refuse"}:
            action = "refuse"
        hook = str(raw.get("hook") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if action == "keep" and not hook:
            action = "refuse"
            reason = reason or "未给出可玩点"
        by_id[mid] = PresentedMod(id=mid, action=action, hook=hook, reason=reason)

    # 按输入顺序对齐；LLM 漏掉的 id 视为 refuse
    ordered: list[PresentedMod] = []
    for c in candidates:
        mid = str(c["id"])
        ordered.append(
            by_id.get(
                mid,
                PresentedMod(id=mid, action="refuse", reason="模型未返回该模组"),
            )
        )
    return PresentResult(items=ordered, used_llm=True)


def select_presented(
    candidates: list[dict],
    present: PresentResult,
    *,
    display_n: int = DISPLAY_N,
) -> tuple[list[dict], list[PresentedMod]]:
    """把 keep 结果合并回候选 dict（写入 hook），截到 display_n。"""
    present_by_id = {p.id: p for p in present.items}
    kept: list[dict] = []
    kept_meta: list[PresentedMod] = []
    for c in candidates:
        mid = str(c["id"])
        p = present_by_id.get(mid)
        if not p or p.action != "keep":
            continue
        row = dict(c)
        row["hook"] = p.hook
        kept.append(row)
        kept_meta.append(p)
        if len(kept) >= display_n:
            break
    # 重排展示 rank
    for i, row in enumerate(kept, start=1):
        row["rank"] = i
    return kept, kept_meta


EMPTY_AFTER_PRESENT = (
    "这批候选里，暂时没有能从简介里清楚讲出「可玩点」、又贴合你问题的模组。"
    "可以换个说法再问，或点「惊喜」换一批发现。"
)
