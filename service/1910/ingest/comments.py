"""经本机代理拉工坊评论并精选真正的好评。"""

from __future__ import annotations

import html as htmlmod
import json
import re
import ssl
import urllib.parse
import urllib.request
from typing import Any

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_TAG = re.compile(r"<[^>]+>")
_TEXT = re.compile(
    r'class="commentthread_comment_text"[^>]*>(.*?)</div>',
    re.I | re.S,
)

# 必须像在夸模组，而不是随口带一个 great
_POS = re.compile(
    r"(?:"
    r"excellent\s+work|fantastic\s+mod|great\s+mod|love\s+(?:your\s+)?mod|"
    r"best\s+(?:\w+\s+){0,3}mod|masterpiece|amazing\s+mod|wonderful\s+mod|"
    r"enjoying\s+it|highly\s+recommend|will\s+play|"
    r"toller\s+mod|genialer?\s+mod|magnifique|super\s+mod|"
    r"神作|神模组|好模组|太牛|牛逼|必玩|必下|推荐玩|强烈推荐|"
    r"做得?很?好|内容丰富|更新速度|沉浸|风味.*丰富|一流|完美适配|"
    r"喜欢这个模组|喜欢这模组|好玩|精彩"
    r")",
    re.I,
)

_NEG = re.compile(
    r"(?:"
    r"闪退|崩溃|卡死|卡顿|黑屏|报错|兼容|无法启动|打不开|"
    r"为什么|怎么|没有找到|出不来|缺失|少得|太少|吐槽|差评|"
    r"crash|ctd|bug|error|broken|unspielbar|unplayable|"
    r"doesn'?t\s+work|can'?t\s+(?:play|start|build)|cannot|"
    r"issue|problem|fix\s+(?:please|this)|not\s+working|"
    r"randomly|billion\s+pops|game\s+crashed|"
    r"is\s+there\s+no|does\s+the\s+mod\s+support|"
    r"sour\s+feeling|didn'?t\s+actually\s+work"
    r")",
    re.I,
)

# 作者回复 / 更新公告 / 纯感谢，不当成玩家好评
_SKIP = re.compile(
    r"(?:"
    r"^@|"  # 回复串
    r"by\s*天草|we\s+have\s+created|i\s+have\s+now\s+fixed|"
    r"i\s+am\s+working\s+on|the\s+great\s+update\s+for\s+mod|"
    r"iw\s+has\s+been\s+perfectly|更新内容现已发布|存档不兼容|"
    r"^thank(?:s| you)[!.,\s]*$|"
    r"^thanks that sounds great|"
    r"could you|would it be possible|please add|"
    r"\bbut i\b|\bhowever\b|希望后续|建议将"
    r")",
    re.I,
)


def _opener(proxy: str) -> urllib.request.OpenerDirector:
    ctx = ssl._create_unverified_context()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
        urllib.request.HTTPSHandler(context=ctx),
    )


_BR = re.compile(r"<br\s*/?>", re.I)


def _inner_html_by_id(page: str, element_id: str) -> str:
    """按 id 取出元素完整 innerHTML（支持嵌套 div）。"""
    m = re.search(
        rf'<div[^>]*\bid=["\']?{re.escape(element_id)}["\']?[^>]*>',
        page,
        re.I,
    )
    if not m:
        return ""
    start = m.end()
    depth = 1
    i = start
    while i < len(page) and depth > 0:
        open_at = page.find("<div", i)
        close_at = page.find("</div>", i)
        if close_at < 0:
            break
        if open_at >= 0 and open_at < close_at:
            # 避免误匹配 <divxxx 之外的标签；Steam 页这里够用
            depth += 1
            i = open_at + 4
            continue
        depth -= 1
        if depth == 0:
            return page[start:close_at]
        i = close_at + 6
    return ""


def _steam_desc_to_plain(inner: str) -> str:
    blob = _BR.sub("\n", inner)
    blob = re.sub(r"</(?:p|div|li|h[1-6]|tr)>", "\n", blob, flags=re.I)
    blob = re.sub(r"<hr\b[^>]*>", "\n", blob, flags=re.I)
    blob = htmlmod.unescape(_TAG.sub("", blob))
    blob = re.sub(r"[ \t]+\n", "\n", blob)
    blob = re.sub(r"\n{3,}", "\n\n", blob).strip()
    return blob


def fetch_workshop_description_zh(
    *,
    file_id: str,
    proxy: str = "http://127.0.0.1:26561",
) -> str:
    """工坊页 l=schinese 的中文简介（纯文本，保留换行）。"""
    opener = _opener(proxy)
    url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={file_id}&l=schinese"
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    with opener.open(req, timeout=30) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    inner = _inner_html_by_id(page, "highlightContent")
    if not inner:
        return ""
    return _steam_desc_to_plain(inner)


def fetch_comments(
    *,
    creator: str,
    file_id: str,
    proxy: str = "http://127.0.0.1:26561",
    pages: int = 12,
    count: int = 50,
) -> list[str]:
    opener = _opener(proxy)
    texts: list[str] = []
    url = (
        "https://steamcommunity.com/comment/PublishedFile_Public/render/"
        f"{creator}/{file_id}/"
    )
    for page in range(pages):
        body = urllib.parse.urlencode(
            {"start": page * count, "count": count, "feature2": -1}
        ).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"https://steamcommunity.com/sharedfiles/filedetails/?id={file_id}",
            },
            method="POST",
        )
        try:
            with opener.open(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            break
        blob = str(payload.get("comments_html") or payload.get("commentshtml") or "")
        if not blob:
            break
        for m in _TEXT.finditer(blob):
            t = htmlmod.unescape(_TAG.sub("", m.group(1)))
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                texts.append(t)
        total = int(payload.get("total_count") or 0)
        if (page + 1) * count >= total:
            break
    return texts


def _score(text: str) -> int:
    """越高越像可展示的好评。"""
    if len(text) < 12 or len(text) > 420:
        return -1
    if _SKIP.search(text) or _NEG.search(text):
        return -1
    if not _POS.search(text):
        return -1
    score = 10
    # 短而干脆的夸赞加分；长篇建议里夹一句 love 减分
    if len(text) <= 120:
        score += 8
    elif len(text) <= 220:
        score += 4
    if _CJK.search(text):
        score += 1  # 略偏中文，方便官网
    # 纯夸、少问号
    if "?" not in text and "？" not in text:
        score += 3
    if text.count("!") + text.count("！") >= 1:
        score += 1
    # 夹带建议/抱怨的不算干净好评
    if re.search(
        r"\bbut\b|\bhowever\b|could you|would it be|please add|希望|建议|不过|但是",
        text,
        re.I,
    ):
        score -= 6
    return score


def pick_reviews(texts: list[str], *, limit: int = 8) -> list[dict[str, Any]]:
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for t in texts:
        key = t.casefold()
        if key in seen:
            continue
        seen.add(key)
        s = _score(t)
        if s < 0:
            continue
        ranked.append((s, t))
    ranked.sort(key=lambda x: (-x[0], len(x[1])))

    non_cjk: list[str] = []
    cjk: list[str] = []
    for _, t in ranked:
        (cjk if _CJK.search(t) else non_cjk).append(t)

    picked: list[str] = []
    # 非中文评论优先凑到一半，其余用中文；不够则互填（对外不标注语言）
    want_other = max(3, limit // 2)
    for t in non_cjk[:want_other]:
        picked.append(t)
    for t in cjk:
        if len(picked) >= limit:
            break
        picked.append(t)
    for t in non_cjk[want_other:]:
        if len(picked) >= limit:
            break
        picked.append(t)
    return [{"text": t} for t in picked[:limit]]
