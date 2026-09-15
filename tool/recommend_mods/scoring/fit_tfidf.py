from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

from .catalog import ModRecord
from .query_text import extract_quoted_phrases, strip_query_shell

# 中英混排：字符 n-gram，免分词依赖
_VECTORIZER_KW = dict(
    analyzer="char_wb",
    ngram_range=(2, 4),
    min_df=2,
    max_df=0.95,
    sublinear_tf=True,
    dtype=np.float32,
)
# 标题更短、专名更稀：min_df=1，避免「汉化洋名」类独占标题被滤掉
_TITLE_VECTORIZER_KW = {**_VECTORIZER_KW, "min_df": 1}

_WS = re.compile(r"\s+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,24}")

# 稀有子串加成（Fit01 量纲）
RARE_DF_MAX = 5  # 全库 title+desc 出现次数 ≤ 此值视为稀有
BOOST_QUOTE_TITLE = 0.22
BOOST_QUOTE_DESC = 0.12
BOOST_RARE_TITLE = 0.15
BOOST_RARE_DESC = 0.08
BOOST_CAP = 0.35


def _clean(text: str) -> str:
    text = (text or "").replace("\x00", " ")
    # Steam 简介常有 [h1]/url] 等 BBCode，粗剥一层
    text = re.sub(r"\[/?[^\]]+\]", " ", text)
    return _WS.sub(" ", text).strip()


@dataclass
class FitParts:
    fit: float
    sim_title: float
    sim_description: float
    sim_tags: float
    used_description: bool
    used_tags: bool
    rare_boost: float = 0.0


class FitTfidf:
    """按字段分别建 TF-IDF，再按 0.25/0.60/0.15 合成 Fit；外加稀有/引号加成。"""

    def __init__(self, mods: list[ModRecord]):
        self.mods = mods
        self.titles = [_clean(m.title_for_fit) for m in mods]
        self.descs = [_clean(m.description) for m in mods]
        self.tags = [_clean(m.tag_text) for m in mods]
        self.has_desc = [bool(d) for d in self.descs]
        self.has_tags = [bool(t) for t in self.tags]
        # 稀有匹配用：小写拉丁 + 原样中文
        self._hay_title = [t.lower() for t in self.titles]
        self._hay_desc = [d.lower() for d in self.descs]
        self._hay_both = [f"{t}\n{d}" for t, d in zip(self._hay_title, self._hay_desc)]

        self.vec_title = TfidfVectorizer(**_TITLE_VECTORIZER_KW)
        self.vec_desc = TfidfVectorizer(**_VECTORIZER_KW)
        self.vec_tags = TfidfVectorizer(**_VECTORIZER_KW)

        # 空串会让部分模型不稳：用占位，相似度自然接近 0
        titles_f = [t if t else " " for t in self.titles]
        descs_f = [d if d else " " for d in self.descs]
        tags_f = [t if t else " " for t in self.tags]

        self.X_title = normalize(self.vec_title.fit_transform(titles_f))
        self.X_desc = normalize(self.vec_desc.fit_transform(descs_f))
        self.X_tags = normalize(self.vec_tags.fit_transform(tags_f))

    def fit_all(self, query: str) -> list[FitParts]:
        q = _clean(query)
        if not q:
            return [
                FitParts(0.0, 0.0, 0.0, 0.0, self.has_desc[i], self.has_tags[i], 0.0)
                for i in range(len(self.mods))
            ]

        qt = normalize(self.vec_title.transform([q]))
        qd = normalize(self.vec_desc.transform([q]))
        qg = normalize(self.vec_tags.transform([q]))

        st = cosine_similarity(qt, self.X_title).ravel()
        sd = cosine_similarity(qd, self.X_desc).ravel()
        sg = cosine_similarity(qg, self.X_tags).ravel()

        anchors = self._rare_anchors(q)
        out: list[FitParts] = []
        for i in range(len(self.mods)):
            base = self._combine(float(st[i]), float(sd[i]), float(sg[i]), i)
            boost = self._rare_boost_for(i, anchors) if anchors else 0.0
            fit = max(0.0, min(1.0, base.fit + boost))
            out.append(
                FitParts(
                    fit=fit,
                    sim_title=base.sim_title,
                    sim_description=base.sim_description,
                    sim_tags=base.sim_tags,
                    used_description=base.used_description,
                    used_tags=base.used_tags,
                    rare_boost=boost,
                )
            )
        return out

    def _combine(self, st: float, sd: float, sg: float, i: int) -> FitParts:
        st = max(0.0, min(1.0, st))
        sd = max(0.0, min(1.0, sd))
        sg = max(0.0, min(1.0, sg))
        has_d = self.has_desc[i]
        has_t = self.has_tags[i]

        if has_d and has_t:
            fit = 0.25 * st + 0.60 * sd + 0.15 * sg
        elif has_d and not has_t:
            # 无 tags：title/description 重归一
            fit = (0.25 * st + 0.60 * sd) / 0.85
        elif (not has_d) and has_t:
            # 无 description：title+tags 重归一，再 ×0.85
            fit = ((0.25 * st + 0.15 * sg) / 0.40) * 0.85
        else:
            fit = st * 0.85
        fit = max(0.0, min(1.0, fit))
        return FitParts(fit, st, sd, sg, has_d, has_t, 0.0)

    def _candidate_phrases(self, query: str) -> list[tuple[str, bool]]:
        """(phrase, is_quoted)。先引号，再剥壳后的 CJK/拉丁片段。"""
        out: list[tuple[str, bool]] = []
        seen: set[str] = set()
        for phrase in extract_quoted_phrases(query):
            key = phrase.lower()
            if key not in seen:
                seen.add(key)
                out.append((phrase, True))
        stripped = strip_query_shell(query)
        for m in _CJK_RUN.finditer(stripped):
            run = m.group(0)
            if len(run) < 2:
                continue
            # 整段 ≤6 直接用；更长则滑窗 3–4（2 字太噪）
            spans = [run] if 2 <= len(run) <= 6 else []
            if len(run) > 6:
                for n in (4, 3):
                    for i in range(len(run) - n + 1):
                        spans.append(run[i : i + n])
            for phrase in spans:
                if not (2 <= len(phrase) <= 6):
                    continue
                key = phrase.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append((phrase, False))
        for m in _LATIN_RUN.finditer(stripped):
            phrase = m.group(0)
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((phrase, False))
        return out

    def _df(self, phrase: str) -> int:
        needle = phrase.lower()
        return sum(1 for hay in self._hay_both if needle in hay)

    def _rare_anchors(self, query: str) -> list[tuple[str, bool]]:
        """保留：全部引号短语；以及 df≤RARE_DF_MAX 的内容片段（不与引号子串重复计）。"""
        candidates = self._candidate_phrases(query)
        quotes = [p for p, q in candidates if q]
        anchors: list[tuple[str, bool]] = []
        for phrase, is_quoted in candidates:
            if is_quoted:
                anchors.append((phrase, True))
                continue
            # 已是某引号短语的子串 → 跳过，避免司徒/司徒雷/徒雷登叠满封顶
            if any(phrase in q or q in phrase for q in quotes):
                continue
            df = self._df(phrase)
            if 1 <= df <= RARE_DF_MAX:
                anchors.append((phrase, False))
        return anchors

    def _rare_boost_for(self, i: int, anchors: list[tuple[str, bool]]) -> float:
        title = self._hay_title[i]
        desc = self._hay_desc[i]
        boost = 0.0
        rare_title = False
        rare_desc = False
        for phrase, is_quoted in anchors:
            needle = phrase.lower()
            in_title = needle in title
            in_desc = needle in desc
            if not in_title and not in_desc:
                continue
            if is_quoted:
                if in_title:
                    boost += BOOST_QUOTE_TITLE
                elif in_desc:
                    boost += BOOST_QUOTE_DESC
            else:
                if in_title:
                    rare_title = True
                elif in_desc:
                    rare_desc = True
        # 非引号稀有：title/desc 各最多加一次，避免滑窗叠满
        if rare_title:
            boost += BOOST_RARE_TITLE
        elif rare_desc:
            boost += BOOST_RARE_DESC
        return min(BOOST_CAP, boost)
