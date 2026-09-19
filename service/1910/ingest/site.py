"""从模组与工坊 sqlite 抽出介绍 / 推荐国家 / 攻略。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from ingest import loc as locmod
from ingest import paradox as px
from paths import WORKSHOP_ID

FUSED_MODS = [
    "国际联盟",
    "20世纪法律",
    "20世纪意识形态",
    "20世纪公司",
    "纯色占领区",
]

TIPS = [
    {
        "title": "大清：释放湖北，卡掉辛亥革命",
        "body": "只要在释放附属国页面将湖北释放出来，释放为附属国或独立国家都行，就能卡掉辛亥革命事件链，无事发生。辛亥革命事件链需要大清拥有湖北。",
    },
    {
        "title": "参战弹窗：游戏规则改为只有列强",
        "body": "建议将参战事件的 gamerule 改为只有列强，否则会被弹窗烦死。",
    },
]

WORLDLINE_ENTER = {
    "TGW": "开局默认进入（history/global 设置全局变量 TGW）。",
    "IW": "第一次世界大战已进行，且德国被羞辱（同盟国战败）。随后移除 TGW。",
    "KR": "第一次世界大战已进行，且法国被羞辱（协约国战败）。随后移除 TGW。",
    "CW": "战间期世界大战已进行，且德国被羞辱。随后移除 IW。",
    "TNO": "战间期世界大战已进行，且苏联 / 英国 / 中国等被羞辱（轴心获胜分支）。随后移除 IW。",
    "KN": "Kaiserreich 世界大战已进行，且德国（或加拿大）被羞辱。随后移除 KR。",
    "KRG": "Kaiserreich 世界大战已进行，且俄国 / 法国被羞辱。随后移除 KR。",
}

WORLDLINE_ORDER = ["TGW", "IW", "KR", "CW", "TNO", "KN", "KRG"]
WORLDLINE_JE = {
    "TGW": "je_tgwshijiexian",
    "IW": "je_iwshijiexian",
    "KR": "je_krshijiexian",
    "CW": "je_cwshijiexian",
    "TNO": "je_tnoshijiexian",
    "KN": "je_knshijiexian",
    "KRG": "je_krgshijiexian",
}

CONTACT_KEYS = [
    "setting_qqqunhao",
    "setting_qqzhanghao",
    "setting_youxiangzhanghao",
    "setting_dianhuahaoma",
    "setting_bzhanhao",
    "setting_tiebahao",
    "setting_douyin",
    "setting_kuaishou",
    "setting_xiaohongshu",
]

# 工坊简介里的群号；loc 常写作「详见创意工坊介绍」
QQ_GROUP_FALLBACK = "527945089"
_QQ_GROUP_RE = re.compile(r"QQ\s*群号\s*[：:]\s*(\d{5,})")

_BB_H1 = re.compile(r"\[h1\](.*?)\[/h1\]", re.I | re.S)
_BB_H2 = re.compile(r"\[h2\](.*?)\[/h2\]", re.I | re.S)
_BB_B = re.compile(r"\[b\](.*?)\[/b\]", re.I | re.S)
_BB_I = re.compile(r"\[i\](.*?)\[/i\]", re.I | re.S)
_BB_U = re.compile(r"\[u\](.*?)\[/u\]", re.I | re.S)


def bbcode_to_html(src: str) -> str:
    text = (
        src.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    text = _BB_H1.sub(r"<h2>\1</h2>", text)
    text = _BB_H2.sub(r"<h3>\1</h3>", text)
    text = _BB_B.sub(r"<strong>\1</strong>", text)
    text = _BB_I.sub(r"<em>\1</em>", text)
    text = _BB_U.sub(r"<u>\1</u>", text)
    text = re.sub(r"\[list\].*?\[/list\]", lambda m: _list_html(m.group(0)), text, flags=re.I | re.S)
    text = re.sub(r"\[/?[a-z0-9]+\]", "", text, flags=re.I)
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    html = []
    for p in parts:
        if p.startswith("<h"):
            html.append(p)
        else:
            html.append("<p>" + p.replace("\n", "<br>") + "</p>")
    return "\n".join(html)


def _list_html(blob: str) -> str:
    items = re.findall(r"\[\*\](.*?)(?=\[\*\]|\[/list\])", blob, re.I | re.S)
    if not items:
        return blob
    lis = "".join(f"<li>{i.strip()}</li>" for i in items)
    return f"<ul>{lis}</ul>"


def count_events(mod: Path) -> int:
    n = 0
    for path in px.iter_txt(mod / "events"):
        n += px.read_text(path).count("type = country_event")
        n += px.read_text(path).count("type=country_event")
    return n


def count_characters(mod: Path) -> int:
    n = 0
    for path in px.iter_txt(mod / "common" / "history" / "characters"):
        n += len(re.findall(r"create_character\s*=", px.read_text(path)))
    return n


def count_loc_keys(mod: Path) -> int:
    loc_dir = mod / "localization" / "simp_chinese"
    return sum(locmod.count_keys(p) for p in loc_dir.rglob("*.yml")) if loc_dir.is_dir() else 0


def start_date(mod: Path) -> str:
    path = mod / "common" / "defines" / "jieshushijian_defines.txt"
    if path.is_file():
        m = px._START_DATE.search(px.read_text(path))
        if m:
            return m.group(1)
    return "1910.1.1"


def start_country_files(mod: Path) -> list[str]:
    tags: list[str] = []
    folder = mod / "common" / "history" / "countries"
    if not folder.is_dir():
        return tags
    for path in sorted(folder.glob("*.txt")):
        if path.name.startswith("顺序"):
            continue
        text = px.read_text(path)
        m = px._COUNTRY_HEADER.search(text)
        if m:
            tags.append(m.group(1))
    return tags


def buildings(mod: Path, loc: dict[str, str]) -> list[dict[str, str]]:
    path = mod / "common" / "buildings" / "iw_buildings.txt"
    out: list[dict[str, str]] = []
    if not path.is_file():
        return out
    for key in px._BUILDING.findall(px.read_text(path)):
        out.append({"id": key, "name": locmod.lookup(loc, key, key)})
    return out


def contacts(
    loc: dict[str, str],
    *,
    qq_group: str = "",
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    group_no = (qq_group or "").strip() or QQ_GROUP_FALLBACK
    for key in CONTACT_KEYS:
        label = loc.get(key)
        if not label and key != "setting_qqqunhao":
            continue
        if key == "setting_qqqunhao":
            # 官网直接展示群号，不写「详见/见下方」
            text = f"作者QQ群号：{group_no}"
        else:
            text = label
        items.append({"id": key, "text": text})
    return items


def extract_qq_group(text: str) -> str:
    m = _QQ_GROUP_RE.search(text or "")
    return m.group(1) if m else ""


def workshop_description(
    db: Path,
    *,
    zh_text: str = "",
) -> dict[str, Any]:
    empty = {"title": "Interwar 1910-1960", "bbcode": "", "html": "", "author": ""}
    title = empty["title"]
    author = ""
    en_desc = ""
    if db.is_file():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT title, description, author FROM details WHERE id = ?",
                (WORKSHOP_ID,),
            ).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            con.close()
        if row:
            title = (row[0] or title) or title
            en_desc = row[1] or ""
            author = row[2] or ""
    # 优先中文工坊页；sqlite 里通常只有英文 BBCode
    src = (zh_text or "").strip()
    if src:
        return {
            "title": title,
            "bbcode": src,
            "html": plain_to_html(src),
            "author": author,
        }
    if en_desc:
        return {
            "title": title,
            "bbcode": en_desc,
            "html": bbcode_to_html(en_desc),
            "author": author,
        }
    return empty


def plain_to_html(src: str) -> str:
    esc = (
        src.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    parts = [p.strip() for p in re.split(r"\n\s*\n", esc) if p.strip()]
    return "\n".join(
        "<p>" + p.replace("\n", "<br>") + "</p>" for p in parts
    )


def objectives(mod: Path, loc: dict[str, str]) -> list[dict[str, Any]]:
    path = mod / "common" / "objectives" / "00_objective_tutorial.txt"
    groups: list[dict[str, Any]] = []
    if not path.is_file():
        return groups
    text = px.read_text(path)
    for m in px._OBJ.finditer(text):
        oid = m.group(1)
        block = px.extract_block(text, m.end() - 1)
        tm = px._TAGS.search(block)
        tags = tm.group(1).split() if tm else []
        countries = []
        for tag in tags:
            countries.append(
                {
                    "tag": tag,
                    "name": locmod.lookup(
                        loc,
                        f"{oid}_name_{tag}",
                        tag,
                        f"COUNTRY_{tag}",
                    ),
                    "flavor": locmod.lookup(loc, f"{oid}_desc_{tag}", default=""),
                }
            )
        groups.append(
            {
                "id": oid,
                "name": locmod.lookup(loc, oid, oid),
                "desc": locmod.lookup(loc, f"{oid}_desc", default=""),
                "countries": countries,
            }
        )
    return groups


def worldlines(loc: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for wid in WORLDLINE_ORDER:
        je = WORLDLINE_JE[wid]
        out.append(
            {
                "id": wid,
                "name": locmod.lookup(loc, je, wid, wid + "世界线"),
                "desc": locmod.lookup(loc, f"{je}_reason", default=""),
                "enter": WORLDLINE_ENTER[wid],
            }
        )
    return out


def flavor_by_tag(groups: list[dict[str, Any]]) -> dict[str, str]:
    flav: dict[str, str] = {}
    for g in groups:
        for c in g["countries"]:
            if c["flavor"] and c["tag"] not in flav:
                flav[c["tag"]] = c["flavor"]
    return flav


def parse_country_history(
    mod: Path,
    loc: dict[str, str],
    *,
    tech_effects: dict[str, Any] | None = None,
    era_techs: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    from ingest.catalogs import expand_tech_ids

    folder = mod / "common" / "history" / "countries"
    by_tag: dict[str, dict[str, Any]] = {}
    if not folder.is_dir():
        return by_tag
    effects = tech_effects or {}
    eras = era_techs or {}
    for path in folder.glob("*.txt"):
        text = px.read_text(path)
        m = px._COUNTRY_HEADER.search(text)
        if not m:
            continue
        tag = m.group(1)
        laws = []
        seen_law: set[str] = set()
        for law_id in px._LAW.findall(text):
            if law_id in seen_law:
                continue
            seen_law.add(law_id)
            laws.append(
                {
                    "id": law_id,
                    "name": locmod.lookup(loc, law_id, law_id),
                    "amendments": [],
                }
            )
        raw_tokens: list[str] = []
        for groups in px._TECH_TOKEN.findall(text):
            token = next((g for g in groups if g), "")
            if token:
                raw_tokens.append(token)
        tech_ids = expand_tech_ids(raw_tokens, effects=effects, era_techs=eras)
        techs = [
            {"id": tid, "name": locmod.lookup(loc, tid, tid)} for tid in tech_ids
        ]
        by_tag[tag] = {"laws": laws, "techs": techs}
    return by_tag


def parse_state_regions(vanilla: Path, mod: Path) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    files: list[Path] = []
    for root in (vanilla, mod):
        d = root / "map_data" / "state_regions"
        if d.is_dir():
            files.extend(sorted(p for p in d.glob("*.txt") if "资源全开" not in p.name))
    for path in files:
        text = px.strip_comments(px.read_text(path))
        for m in px._STATE.finditer(text):
            sid = m.group(1)
            block = px.extract_block(text, m.end() - 1)
            arable = 0
            am = px._ARABLE.search(block)
            if am:
                arable = int(am.group(1))
            resources: dict[str, int] = {}
            for kind in ("capped_resources", "resource"):
                km = re.search(kind + r"\s*=\s*\{", block)
                if not km:
                    continue
                inner = px.extract_block(block, km.end() - 1)
                for rm in re.finditer(r"(building_[a-z0-9_]+)\s*=\s*(\d+)", inner):
                    resources[rm.group(1)] = int(rm.group(2))
            disc = re.search(r"discovered_resources\s*=\s*\{", block)
            if disc:
                inner = px.extract_block(block, disc.end() - 1)
                for rm in re.finditer(
                    r"(building_[a-z0-9_]+)\s*=\s*\{[^}]*?(?:discovered_amount|amount)\s*=\s*(\d+)",
                    inner,
                    re.S,
                ):
                    resources[rm.group(1)] = resources.get(rm.group(1), 0) + int(rm.group(2))
            provs = re.findall(r'"?(x[0-9A-Fa-f]{6})"?', block)
            states[sid] = {
                "arable_land": arable,
                "resources": resources,
                "provinces": [p.lower() for p in provs],
            }
    return states
