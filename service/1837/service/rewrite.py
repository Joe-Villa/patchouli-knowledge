"""门控：refuse / rewrite(+硬过滤) / surprise。DeepSeek OpenAI 兼容 API。"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from scoring.filters import HardFilterSpec, KNOWN_TAGS, parse_hard_filter
from scoring.query_text import enrich_rewrite, fallback_rewrite

_TAG_LIST = "、".join(KNOWN_TAGS)

_SYSTEM = f"""你是维多利亚3创意工坊模组推荐的门控器。库里全是 Vic3 模组。

先判断用户是否在找/推/筛模组；再在 refuse / surprise / rewrite 三者中选一。

应 refuse（仅此，从严）：
- 明显不是模组推荐：闲聊、骂人、政治、解题、写代码、问原版机制细节、角色扮演、明显其他游戏且无关、乱码
- 不要因为「需求糊」「只有约束没有题材」「想看分类」而 refuse

应 surprise（完全没有题材/口味主见 → 走「给我惊喜」管线）：
- 「好玩」「有啥模组」「不知道下啥」「随便推几个」「看看都有啥类」「给我惊喜」等
- 只有硬约束（体积/更新时效/订阅门槛）而没有任何题材/玩法方向时，也 surprise，并把约束写入 filters
- 禁止编造「热门/必装/精品」之类空检索词来假装 rewrite

应 rewrite（有方向，或可合理猜测补全）：
1. query：语义不变的短检索词；中英可并列；不要写「维多利亚3/Vic3/模组推荐」
   - 去掉口语壳：有没有/请问/就像…那样/模组/推荐一下/之类的
   - 保留：专名、机制词、国别、引号内例子（务必保留）
   - 糊但有线索时可猜测补全（如「变强加速」→ 加成 buff cheat；「更好看的人物」→ 立绘 portrait）
   - 宜短：空格分隔关键词，避免整句口语
2. filters：仅在用户明确提出时填写，否则省略
   可用字段：
   - min_subscribers / max_subscribers（整数）
   - max_file_size_mb（数字，如「别好几G/笔记本」→ 例如 500 或 200）
   - updated_within_days（整数，如「两三年没更新别推」→ 730；「最近还在更新」→ 180）
   - require_tags_any / require_tags_all / exclude_tags_any（字符串数组）
   - exclude_title_keywords（标题子串数组）
   常见 tags：{_TAG_LIST}
   例：不要大改/总转换 → exclude_tags_any:["Total Conversion"]
   例：只要音乐 → require_tags_any:["Sound"]（若用户只说音乐也可用 query 承载，tag 非必须）

示例：
用户：有没有将洋人的名字转化为中华名字的模组，就像「司徒雷登」那样？
→ {{"action":"rewrite","query":"洋名 中华名 汉化 司徒雷登 音译","filters":{{}}}}
用户：巴西风味事件有哪些
→ {{"action":"rewrite","query":"巴西 风味 事件 Brazil flavor","filters":{{}}}}
用户：推荐一些比较好玩的模组
→ {{"action":"surprise","filters":{{}}}}
用户：有啥模组啊，不知道下啥
→ {{"action":"surprise","filters":{{}}}}
用户：两三年没更新的别推，怕坏了
→ {{"action":"surprise","filters":{{"updated_within_days":730}}}}
用户：小一点的，别好几G，笔记本跑不动
→ {{"action":"surprise","filters":{{"max_file_size_mb":500}}}}
用户：有没有单纯给加成的，变强加速一类的
→ {{"action":"rewrite","query":"加成 加速 变强 buff cheat","filters":{{"exclude_tags_any":["Total Conversion"]}}}}

只输出一个 JSON：
{{"action":"surprise","filters":{{...}}}}
或
{{"action":"rewrite","query":"...","filters":{{...}}}}
或
{{"action":"refuse","reason":"..."}}
filters 里未用到的键可省略；不要 markdown。"""

# 仅由这些空壳词构成的 rewrite → 视为无主见，降级 surprise
_FLUFF_TOKENS = {
    "热门",
    "推荐",
    "必装",
    "好玩",
    "有趣",
    "模组",
    "精品",
    "随便",
    "看看",
    "有啥",
    "什么",
    "一类",
    "popular",
    "recommend",
    "recommended",
    "fun",
    "best",
    "mods",
    "mod",
    "interesting",
    "cool",
}


@dataclass(frozen=True)
class RewriteConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout_sec: float = 60.0


@dataclass(frozen=True)
class GateResult:
    action: str  # "rewrite" | "refuse" | "surprise"
    query: str = ""
    reason: str = ""
    filters: HardFilterSpec = field(default_factory=HardFilterSpec)


def load_dotenv_files() -> None:
    roots = [
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parents[2] / "deploy" / ".env",
    ]
    for path in roots:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k, v = k.strip(), v.strip().strip("'").strip('"')
            if k and k not in os.environ:
                os.environ[k] = v


def load_rewrite_config() -> RewriteConfig:
    load_dotenv_files()
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    return RewriteConfig(
        api_key=key,
        base_url=(os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip(
            "/"
        ),
        model=(os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip(),
        timeout_sec=float(os.environ.get("LLM_TIMEOUT_SEC") or "60"),
    )


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


def _clean_query(q: str) -> str:
    q = (q or "").strip()
    q = re.sub(r"^(规范化问句|转义|改写)\s*[:：]\s*", "", q)
    if (q.startswith('"') and q.endswith('"')) or (q.startswith("「") and q.endswith("」")):
        q = q[1:-1].strip()
    return q.splitlines()[0].strip() if q else ""


def _tokenize(text: str) -> set[str]:
    parts = re.split(r"[\s,，、|/；;]+", (text or "").strip().lower())
    return {p for p in parts if p}


def rewrite_is_fluff(query: str) -> bool:
    """rewrite 词是否只剩空壳（热门/好玩/推荐…）。"""
    toks = _tokenize(query)
    return bool(toks) and toks <= _FLUFF_TOKENS


def looks_preference_less(user_query: str) -> bool:
    """无 API 时的启发式：完全无主见 / 只要约束。"""
    q = (user_query or "").strip()
    if not q:
        return True
    # 明显题材线索 → 不当 surprise
    topicish = re.search(
        r"(冷战|一战|现代|总转换|剧本|UI|界面|立绘|美化|肖像|BGM|音乐|刀鱼|作弊|"
        r"加成|加速|经济|建筑|贸易|外交|战争|军事|历史|风味|汉化|洋名|"
        r"ui|music|portrait|cheat|cold\s*war|total\s*conversion|"
        r"economy|diplomacy|warfare|flavor)",
        q,
        re.IGNORECASE,
    )
    if topicish:
        return False
    vague = re.search(
        r"(好玩|有趣|有啥|什么模组|不知道下|随便|推几个|有啥类|都有啥|"
        r"惊喜|推荐一些|推荐几个|新人|入坑|不知道装啥)",
        q,
    )
    only_constraint = re.search(
        r"(更新|两三年|老坑|好几\s*G|笔记本|体积|轻量|小一点|最近还在)",
        q,
    ) and not topicish
    return bool(vague) or bool(only_constraint)


def _coerce_fluff_rewrite(result: GateResult) -> GateResult:
    if result.action != "rewrite":
        return result
    if rewrite_is_fluff(result.query):
        return GateResult(action="surprise", filters=result.filters)
    return result


def _parse_gate(content: str) -> GateResult:
    try:
        obj = _extract_json(content)
        action = str(obj.get("action") or "").strip().lower()
        if action == "refuse":
            reason = str(obj.get("reason") or "当前输入不适合做模组推荐。").strip()
            return GateResult(
                action="refuse",
                reason=reason or "当前输入不适合做模组推荐。",
            )
        filters = parse_hard_filter(obj.get("filters") or {})
        if action == "surprise":
            return GateResult(action="surprise", filters=filters)
        if action == "rewrite":
            q = _clean_query(str(obj.get("query") or ""))
            if not q:
                # 空 query 但像推荐意图 → surprise，而不是 fail
                return GateResult(action="surprise", filters=filters)
            return GateResult(action="rewrite", query=q, filters=filters)
    except Exception:
        pass

    s = (content or "").strip()
    if re.match(r"(?i)^refuse\b", s) or s.startswith("拒绝"):
        reason = re.sub(r"(?i)^refuse\s*[:：]?\s*", "", s)
        reason = re.sub(r"^拒绝\s*[:：]?\s*", "", reason).strip() or "当前输入不适合做模组推荐。"
        return GateResult(action="refuse", reason=reason.splitlines()[0].strip())
    if re.match(r"(?i)^surprise\b", s) or s.startswith("惊喜"):
        return GateResult(action="surprise")
    line = _clean_query(s)
    if line:
        return GateResult(action="rewrite", query=line)
    raise RuntimeError("unparseable gate response")


def _finalize_rewrite(original: str, rewritten: str) -> str:
    """补回引号专名；若仍空则规则剥壳。"""
    q = enrich_rewrite(original, rewritten)
    return q or fallback_rewrite(original)


def gate_query(query: str, *, config: RewriteConfig | None = None) -> GateResult:
    q = (query or "").strip()
    if not q:
        return GateResult(action="refuse", reason="没有有效问题。")
    cfg = config or load_rewrite_config()
    if not cfg.api_key:
        # 无密钥：无主见 → surprise；否则规则剥壳 rewrite
        if looks_preference_less(q):
            return GateResult(action="surprise")
        return GateResult(action="rewrite", query=fallback_rewrite(q))

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": q},
        ],
        "stream": False,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg.base_url}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if e.code == 400 and "response_format" in detail:
            payload.pop("response_format", None)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{cfg.base_url}/chat/completions",
                data=data,
                headers={
                    "Authorization": f"Bearer {cfg.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        else:
            raise RuntimeError(f"DeepSeek HTTP {e.code}: {detail[:300]}") from e

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("empty deepseek response")
    content = (choices[0].get("message") or {}).get("content") or ""
    result = _parse_gate(content)
    if result.action == "rewrite":
        finalized = GateResult(
            action="rewrite",
            query=_finalize_rewrite(q, result.query),
            filters=result.filters,
        )
        return _coerce_fluff_rewrite(finalized)
    return result


def rewrite_query(query: str, *, config: RewriteConfig | None = None) -> str:
    r = gate_query(query, config=config)
    if r.action == "refuse":
        raise RuntimeError(f"refused: {r.reason}")
    if r.action == "surprise":
        raise RuntimeError("surprise: no rewrite query")
    return r.query
