"""请求入口：纯文本问题 → 找 → 答 → 综述文本。不含 QQ I/O。每次调用无状态。"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import RequestConfig

_CORE = Path(__file__).resolve().parent.parent
_FIND = _CORE / "find"
_LLM = _CORE / "llm"
_DATA = _CORE / "data"
_SANDBOX = _CORE / "sandbox"


def _ensure_import_paths() -> None:
    for p in (_DATA, _LLM, _SANDBOX, _CORE):
        sp = str(p)
        if sp not in sys.path:
            sys.path.append(sp)
    fd = str(_FIND)
    if fd in sys.path:
        sys.path.remove(fd)
    sys.path.insert(0, fd)


_ensure_import_paths()

from agent import FindResult, find_evidence  # noqa: E402
from answer import AnswerResult, synthesize_answer  # noqa: E402
from client import ChatMessage, LLMConfig, chat, load_llm_config, parse_dotenv  # noqa: E402
from evidence import EvidenceItem, EvidencePackage  # noqa: E402
from mod_catalog import (  # noqa: E402
    CorpusChoice,
    CorpusPin,
    CorpusSide,
    ModCatalog,
    ModInfo,
    STATUS_VANILLAONLY,
    choice_from_ids,
    normalize_status,
    resolve_game_root,
    resolve_pool_mod_roots,
    resolve_side_root,
    select_corpus,
)
from reqlog import RequestLogSession  # noqa: E402

log = logging.getLogger("patchouli.request")


@dataclass
class RequestResult:
    ok: bool
    question: str
    answer: str
    find: FindResult
    answer_result: AnswerResult | None
    elapsed_sec: float = 0.0
    log_path: str | None = None
    """summary.json 路径（新日志系统）。"""
    request_id: str | None = None
    log_dir: str | None = None
    """本次请求的日志目录 requests/{id}/。"""
    config_snapshot: dict | None = None
    corpus: CorpusChoice | None = None
    """内部选定的资料库（原版 / 某模组 / 两方对比）。"""

    @property
    def coverage(self) -> str | None:
        return self.find.package.coverage if self.find else None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


_LLM_ALIAS_DETAIL_CAP = 40


def _question_clearly_vanilla(question: str) -> bool:
    """题干明确指向原版（才允许自动判断结果为 0 个模组）。"""
    return bool(re.search(r"原版|vanilla|官版", question or "", re.IGNORECASE))


def _format_mods_for_llm(options: list[ModInfo]) -> str:
    """类别模组列表：小池带 aliases；大池用紧凑 id/short/name，避免漏选项。"""
    if len(options) <= _LLM_ALIAS_DETAIL_CAP:
        lines = []
        for m in options:
            alias_s = "、".join(a for a in m.aliases if a and a != m.id)[:120]
            lines.append(
                f"- id={m.id} | short={m.short} | name={m.name} | aliases={alias_s}"
            )
        return "\n".join(lines)
    return "\n".join(f"- {m.id}\t{m.short}\t{m.name}" for m in options)


def _map_llm_mod_ids(
    raw_ids: list[Any],
    options: list[ModInfo],
) -> list[str]:
    id_set = {m.id for m in options}
    by_alias: dict[str, str] = {}
    for m in options:
        for a in (m.id, m.short, m.name, *m.aliases):
            key = str(a or "").strip().lower()
            if key:
                by_alias.setdefault(key, m.id)
    mapped: list[str] = []
    for item in raw_ids:
        s = str(item or "").strip()
        if not s:
            continue
        mid = s if s in id_set else by_alias.get(s.lower(), "")
        if mid and mid not in mapped:
            mapped.append(mid)
        if len(mapped) >= 2:
            break
    return mapped


def _llm_pick_corpus(
    question: str,
    catalog: ModCatalog,
    pin: CorpusPin,
    llm: LLMConfig,
) -> CorpusChoice:
    """未选手动 tag、别名未命中时：在当前类别可选模组中让 LLM 选择。

    - 必须先走类别选项，禁止别名未命中就静默只挂原版。
    - 通用：LLM 可选 0 个→原版；其它类别：除非题干明确指原版，否则必须选 1～2 个。
    - 失败 / 非法空选 → rejected。
    """
    status = normalize_status(pin.status)
    options = catalog.mods_for_status(status)
    if not options:
        # 类别无模组：只能原版（与「只看原版」不同：status 仍保留）
        return CorpusChoice(kind="vanilla", reason="llm_auto", status=status)

    catalog_block = _format_mods_for_llm(options)
    status_label = {"common": "通用"}.get(status, status)
    # 通用：可判原版（很多原版题不会写「原版」）。其它类别：除非题干明确指原版，否则必须从该类选项中选。
    allow_vanilla_zero = status == "common" or _question_clearly_vanilla(question)

    system = (
        "你是 Hearts of Iron IV 问答服务的资料库选择器。"
        "用户没有手动选择模组标签。"
        "你必须在「当前类别可选模组」中选择本题该参考的模组。"
        "不要编造不在列表中的 workshop id。"
        "只输出一个 JSON 对象，不要 Markdown。"
    )
    if status == "common":
        zero_rule = (
            "- 0 个：问题明显只问原版机制，与上列模组无关\n"
            "- 1 个：问题主要关于某一个模组\n"
            "- 2 个：需要两个模组对照\n"
        )
    elif allow_vanilla_zero:
        zero_rule = (
            "- 0 个：仅当题干明确在问原版/vanilla/官版，且与上列模组无关\n"
            "- 1 个：问题主要关于某一个模组\n"
            "- 2 个：需要两个模组对照\n"
        )
    else:
        zero_rule = (
            "- 禁止选 0 个：当前类别不是「只看原版」，必须从列表中选 1～2 个最相关模组；"
            "即使没有点名，也要按题意挑最可能的\n"
            "- 1 个：问题主要关于某一个模组\n"
            "- 2 个：需要两个模组对照\n"
        )
    user = (
        f"当前类别：{status_label}（status={status}）\n"
        f"用户问题：{question.strip()}\n\n"
        f"当前类别可选模组（共 {len(options)} 个）：\n{catalog_block}\n\n"
        "请选择模组 id：\n"
        f"{zero_rule}"
        '输出 JSON：{"mod_ids":["..."],"reason":"一句话理由"}'
    )
    try:
        result = chat(
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
            config=llm,
            temperature=0.0,
        )
        data = _parse_json_object(result.message.content or "")
    except Exception:
        log.exception("llm corpus pick failed")
        return CorpusChoice(
            kind="rejected",
            reason="llm_auto_failed",
            status=status,
        )

    if not data:
        log.warning("llm corpus pick: bad json")
        return CorpusChoice(
            kind="rejected",
            reason="llm_auto_failed",
            status=status,
        )

    raw_ids = data.get("mod_ids") or data.get("ids") or []
    if not isinstance(raw_ids, list):
        raw_ids = []
    mapped = _map_llm_mod_ids(raw_ids, options)

    if not mapped:
        if allow_vanilla_zero:
            return CorpusChoice(kind="vanilla", reason="llm_auto", status=status)
        # 非「明确原版」却选了 0：不算自动选择成功
        return CorpusChoice(
            kind="rejected",
            reason="llm_no_match",
            status=status,
        )

    return choice_from_ids(mapped, catalog, status=status, reason="llm_auto")


def _maybe_llm_auto_corpus(
    *,
    question: str,
    choice: CorpusChoice,
    pin: CorpusPin,
    catalog: ModCatalog,
    llm: LLMConfig,
    config: RequestConfig,
    on_progress: Any = None,
) -> CorpusChoice:
    """别名未命中且未锁定时，用 LLM 在类别选项中选择（禁止静默只挂原版）。"""
    if not config.auto_select_corpus:
        return choice
    if pin.is_locked:
        return choice
    if normalize_status(pin.status) == STATUS_VANILLAONLY:
        return choice
    if not (choice.kind == "vanilla" and choice.reason == "no_mod_mentioned"):
        return choice
    options = catalog.mods_for_status(pin.status)
    if not options:
        return choice
    if not llm.api_key:
        # 有可选模组却无法自动判断：拒绝，避免假装「只看原版」
        return CorpusChoice(
            kind="rejected",
            reason="llm_auto_unavailable",
            status=normalize_status(pin.status),
        )
    if on_progress is not None:
        try:
            on_progress({"phase": "corpus", "round": 0, "max_rounds": config.max_rounds})
        except Exception:
            log.debug("on_progress(corpus) failed", exc_info=True)
    return _llm_pick_corpus(question, catalog, pin, llm)


def _load_llm(config: RequestConfig) -> LLMConfig:
    if config.env_file:
        parse_dotenv(Path(config.env_file))
    base = load_llm_config()
    return LLMConfig(
        api_key=(config.api_key or base.api_key or "").strip(),
        base_url=(config.base_url or base.base_url).rstrip("/"),
        model=(config.model or base.model).strip(),
        timeout_sec=float(config.llm_timeout_sec or base.timeout_sec),
    )
    if config.env_file:
        parse_dotenv(Path(config.env_file))
    base = load_llm_config()
    return LLMConfig(
        api_key=(config.api_key or base.api_key or "").strip(),
        base_url=(config.base_url or base.base_url).rstrip("/"),
        model=(config.model or base.model).strip(),
        timeout_sec=float(config.llm_timeout_sec or base.timeout_sec),
    )


def _tag_deixis_hint(labels: list[str]) -> str:
    """已选手动 tag 时的指代提示：可能指已选模组，不是一定。"""
    names = [x.strip() for x in labels if x and str(x).strip()]
    if not names:
        return ""
    if len(names) == 1:
        target = f"「{names[0]}」"
        examples = "「这个」「这些」「该模组」「此模组」"
    else:
        joined = "」与「".join(names)
        target = f"「{joined}」"
        examples = "「这个」「这些」「这两个模组」「它们」"
    return (
        f"指代提示（仅供判断意图，非强制）：题干里的{examples}等，"
        f"有可能指用户已选的模组{target}，也可能指题干或上下文里的别的东西；"
        "请结合语境判断，不要一律当成已选模组。"
    )


def _side_note(side: CorpusSide, *, deixis_labels: list[str] | None = None) -> str:
    if side.kind == "vanilla":
        base = (
            "当前资料库：原版游戏（vanilla）。"
            "所有路径相对本会话挂载的 /game（即原版削减树）。"
            "这是对比题的一方；只收集本树证据，不要假设另一方可在本树读到。"
        )
    elif side.kind == "mod" and side.mod is not None:
        m = side.mod
        base = (
            f"当前资料库：模组「{m.display_name()}」（workshop_id={m.id}）。"
            "本会话 /game 挂的是该模组的削减树（含其 common/events/map_data/history 与 loc）。"
            "只参考本模组内容；不要查阅或臆造其他未选定模组。"
            "必须优先在本模组内定位实体、history、地区与机制；"
            "仅当本模组明确缺失时，才可对照原版缺口（且不得用原版抢先替代本模组已有定义）。"
            "这是对比题的一方；只收集本树证据。"
        )
    else:
        return ""
    hint = _tag_deixis_hint(deixis_labels or [])
    return f"{base}\n{hint}" if hint else base


def _corpus_note(choice: CorpusChoice) -> str:
    if choice.kind == "vanilla":
        locked = choice.reason in {"ui_vanilla_only", "status_vanillaonly"}
        base = (
            "当前资料库：原版游戏（vanilla）。"
            "所有路径相对本会话挂载的 /game（即原版削减树）。"
        )
        if locked:
            return (
                base
                + "模式：只看原版。用户已锁定原版——只能参考原版文件，不要查阅任何模组。"
            )
        if choice.reason == "pool_empty":
            return base + "（当前类别模组池为空，仅原版可用。）"
        return base + "（仅原版。）"
    if choice.kind == "pool":
        st = choice.status
        label = {"common": "通用"}.get(st, st)
        lines = [
            f"模式：类别整池可见（{label}，未选手动标签）。",
            "本会话 /game = 原版削减树；/mods/<workshop_id>/ = 该类别下各模组削减树。",
            "你可以看到本类别全部模组文件；用 run_code 可 ls /mods 计数；",
            "读具体模组用 path=mods/<id>/... 或 glob=mods/<id>/**。",
            "不要假装只能看原版。",
            f"本池共 {len(choice.pool)} 个模组：",
        ]
        for m in choice.pool[:80]:
            lines.append(f"- {m.short}（{m.name}） id={m.id}")
        if len(choice.pool) > 80:
            lines.append(f"…另有 {len(choice.pool) - 80} 个未列出，仍可在 /mods 下访问")
        return "\n".join(lines)
    if choice.kind == "mod" and choice.mod is not None:
        m = choice.mod
        locked = choice.reason == "ui_tag"
        if locked:
            hint = _tag_deixis_hint([m.display_name()])
            return (
                f"模式：标签锁定。用户已选定模组「{m.display_name()}」（workshop_id={m.id}）。"
                "本会话 /game 即为该模组削减树（含 common/events/map_data/history 与 loc）。"
                "只能参考：① 该已选模组；② 必要时的原版（仅当本模组明确缺失、工具结果证明找不到时）。"
                "禁止参考其他未选定模组；禁止用原版同名内容抢先回答或覆盖本模组已有定义。"
                "例如问某国开局地区：先在本模组 history / map_data / countries 定位该国与地区。"
                f"\n{hint}"
            )
        return (
            f"当前资料库：模组「{m.display_name()}」（workshop_id={m.id}）。"
            "本会话 /game 挂的是该模组的削减树（含其 common/events/map_data/history 与 loc）。"
            "优先在本模组内定位实体与 history；不要假设原版同名定义仍有效；"
            "不要查阅其他未点名模组。仅当本模组明确缺失时再说明缺口。"
        )
    if choice.is_compare and len(choice.sides) == 2:
        a, b = choice.sides
        locked = choice.reason == "ui_tags_compare"
        if locked:
            hint = _tag_deixis_hint([a.label(), b.label()])
            return (
                f"模式：标签锁定（双模组）。用户已选定：{a.label()} 与 {b.label()}。"
                "只能参考这两个已选模组；必要时才对照原版缺口。"
                "禁止参考其他未选定模组。"
                "当前会话只挂载其中一方；请只在本树内取证，并优先读己方实体与 history。"
                f"\n{hint}"
            )
        return (
            f"本题为两方对比：{a.label()} vs {b.label()}。"
            "当前会话只会挂载其中一方；请只在本树内取证。"
            "各方均须优先读己方树内的实体与 history。"
        )
    return ""


def _prefix_banner(answer: str, choice: CorpusChoice) -> str:
    banner = choice.banner_line()
    body = (answer or "").strip()
    if not body:
        return banner
    return f"{banner}\n\n{body}"


def _reject_message(choice: CorpusChoice) -> str:
    reason = choice.reason or "rejected"
    if reason == "three_way_unsupported":
        detail = (
            "不支持三方对比（两个模组再加原版）。"
            "请改为「两个模组对比」或「单个模组与原版对比」。"
        )
    elif reason.startswith("too_many_mods") or reason.startswith("pin_too_many"):
        detail = "一次最多对比两个模组。请减少题干中的模组名或标签。"
    elif reason.startswith("ambiguous_mods"):
        detail = (
            "题干命中多个模组但未表达对比意图。"
            "若要对比请写明「对比/不同/差异」等；"
            "若只问其中一个请只保留一个模组名。"
        )
    elif reason.startswith("pin_unknown_mod"):
        detail = "所选标签对应的模组不在当前资料库目录中，请重新选择。"
    elif reason.startswith("pin_wrong_category"):
        detail = (
            "所选模组不属于当前类别（status）。"
            "请切换到对应类别，或改选当前类别下的模组。"
        )
    elif reason == "pin_mods_empty":
        detail = "已选择「按标签锁定」但未指定模组，请添加标签或改回自动选择。"
    elif reason.startswith("llm_unknown_mod") or reason.startswith("llm_wrong_category"):
        detail = "自动判断所选模组无效，请改选手动标签或换种问法。"
    elif reason.startswith("llm_too_many"):
        detail = "自动判断一次最多选两个模组。请减少题干中的模组指向，或改选手动标签。"
    elif reason in {"llm_auto_failed", "llm_auto_unavailable"}:
        detail = (
            "未能在当前类别可选模组中完成自动判断。"
            "请添加模组标签（最多 2 个），或在题干写明模组名/简称后重试。"
        )
    elif reason == "llm_no_match":
        detail = (
            "自动判断未能从当前类别中确定模组。"
            "请添加标签，或把模组名写进题干；若只问原版请点「只看原版」或题干写明「原版」。"
        )
    else:
        detail = (
            "请在题干中写出模组名或 workshop id，或添加标签；"
            "未选手动标签时会在当前类别可选模组中自动判断。"
            "支持：单资料库问答；或两方对比（两模组，或一模组×原版）。"
        )
    return (
        f"{choice.banner_line()}\n\n"
        f"无法唯一确定资料库（{reason}）。{detail}"
    )


def _early_exit(
    *,
    question: str,
    choice: CorpusChoice,
    config: RequestConfig,
    t0: float,
    msg: str,
    stop_reason: str,
    error: str | None,
    extra: dict | None = None,
) -> RequestResult:
    """corpus 拒绝 / 目录缺失：不跑找侧，但仍写完整 summary 字段。"""
    elapsed = time.monotonic() - t0
    empty_find = FindResult(
        ok=False,
        package=EvidencePackage(coverage="empty", question=question),
        question=question,
        stop_reason=stop_reason,
        error=error,
    )
    snap = config.to_dict()
    snap["corpus"] = choice.to_dict()
    req_log = RequestLogSession.create(
        config.log_dir,
        question=question,
        config_snapshot=snap,
    )
    req_log.write_find_detail(
        {
            "ok": False,
            "stop_reason": stop_reason,
            "rounds": 0,
            "tool_calls": 0,
            "elapsed_sec": elapsed,
            "session_id": None,
            "error": error,
            "package": empty_find.package.to_dict(),
            "question": question,
            "skipped": True,
            "skip_reason": stop_reason,
            "transcript": [],
            "llm_calls": [],
        }
    )
    payload_extra = {"corpus": choice.to_dict()}
    if extra:
        payload_extra.update(extra)
    summary_path = req_log.finalize(
        ok=False,
        answer=msg,
        elapsed_sec=elapsed,
        extra=payload_extra,
    )
    return RequestResult(
        ok=False,
        question=question,
        answer=msg,
        find=empty_find,
        answer_result=None,
        elapsed_sec=elapsed,
        log_path=summary_path,
        request_id=req_log.request_id,
        log_dir=str(req_log.dir) if config.log_dir is not None else None,
        config_snapshot=snap,
        corpus=choice,
    )


def _find_detail_payload(find_res: FindResult) -> dict:
    return {
        "ok": find_res.ok,
        "stop_reason": find_res.stop_reason,
        "rounds": find_res.rounds,
        "tool_calls": find_res.tool_calls,
        "elapsed_sec": find_res.elapsed_sec,
        "session_id": find_res.session_id,
        "error": find_res.error,
        "package": find_res.package.to_dict(),
        "question": find_res.question,
        "transcript": find_res.transcript,
    }


def _merge_coverage(a: str, b: str) -> str:
    if a == "ambiguous" or b == "ambiguous":
        return "ambiguous"
    if a == "empty" and b == "empty":
        return "empty"
    if a == "sufficient" and b == "sufficient":
        return "sufficient"
    return "partial"


def _tag_items(pkg: EvidencePackage, label: str) -> list[EvidenceItem]:
    out: list[EvidenceItem] = []
    for it in pkg.items:
        why = it.why or ""
        prefix = f"[{label}] "
        new_why = why if why.startswith(prefix) else (prefix + why if why else f"来自资料库「{label}」")
        out.append(replace(it, source=label, why=new_why))
    return out


def _merge_compare_packages(
    pkg_a: EvidencePackage,
    pkg_b: EvidencePackage,
    *,
    label_a: str,
    label_b: str,
    question: str,
) -> EvidencePackage:
    items = _tag_items(pkg_a, label_a) + _tag_items(pkg_b, label_b)
    resolved = list(pkg_a.resolved_entities) + list(pkg_b.resolved_entities)
    unresolved = list(pkg_a.unresolved) + list(pkg_b.unresolved)
    not_expanded = list(pkg_a.not_expanded) + list(pkg_b.not_expanded)
    notes_parts = [
        f"两方对比：{label_a} vs {label_b}。以下证据块已用 source / why 标明来源。",
    ]
    if pkg_a.notes:
        notes_parts.append(f"[{label_a}] {pkg_a.notes.strip()}")
    if pkg_b.notes:
        notes_parts.append(f"[{label_b}] {pkg_b.notes.strip()}")
    focus = pkg_a.question_focus or pkg_b.question_focus
    return EvidencePackage(
        coverage=_merge_coverage(pkg_a.coverage, pkg_b.coverage),  # type: ignore[arg-type]
        items=items,
        question=question,
        question_focus=focus,
        resolved_entities=resolved,
        unresolved=unresolved,
        not_expanded=not_expanded,
        notes="\n".join(notes_parts),
    )


def _run_find_once(
    *,
    question: str,
    game_root: Path,
    loc_db: Path,
    llm: LLMConfig,
    config: RequestConfig,
    corpus_note: str,
    req_log: RequestLogSession | None,
    on_progress: Any = None,
    progress_side_index: int = 0,
    progress_side_count: int = 1,
    progress_side_label: str | None = None,
    mods_roots: dict[str, Path] | None = None,
) -> FindResult:
    return find_evidence(
        question,
        game_root=game_root,
        loc_db=loc_db,
        llm_config=llm,
        max_rounds=config.max_rounds,
        submit_grace=config.submit_grace,
        max_wall_sec=config.max_wall_sec,
        log_dir=config.find_log_dir if config.legacy_side_logs else None,
        lang=config.lang,
        sessions_root=config.sessions_root,
        request_log=req_log,
        corpus_note=corpus_note,
        on_progress=on_progress,
        progress_side_index=progress_side_index,
        progress_side_count=progress_side_count,
        progress_side_label=progress_side_label,
        mods_roots=mods_roots,
    )


def run_request(
    question: str,
    config: RequestConfig,
    *,
    on_progress: Any = None,
    corpus_pin: CorpusPin | dict | None = None,
) -> RequestResult:
    """一次完整请求。无会话记忆；沙箱在函数内创建并销毁。

    on_progress: 可选回调 dict（phase/round/max_rounds/…），仅处理器阶段触发。
    corpus_pin: 可选。含 status（common|vanillaonly）与 mode/mod_ids；
                 status 决定可见模组池；mode=mods 时锁定标签（最多 2 个）。
    """
    q = (question or "").strip()
    if not q:
        raise ValueError("question 为空")

    t0 = time.monotonic()

    catalog = ModCatalog.load(config.resolved_mods_catalog)
    pin = CorpusPin.from_mapping(corpus_pin)
    # 始终带上 pin：即使 mode=auto，也要用 status 过滤可见模组池
    if pin.is_locked or config.auto_select_corpus:
        choice = select_corpus(q, catalog, pin=pin)
    elif normalize_status(pin.status) == STATUS_VANILLAONLY:
        choice = CorpusChoice(
            kind="vanilla",
            reason="status_vanillaonly",
            status=STATUS_VANILLAONLY,
        )
    else:
        choice = CorpusChoice(
            kind="vanilla",
            reason="auto_select_disabled",
            status=normalize_status(pin.status),
        )

    llm = _load_llm(config)
    # 未选手动 tag 且题干未点名时 select_corpus 已返回 kind=pool（整池可见），
    # 不再用 LLM 收成「只挂原版」。

    if not choice.ok:
        return _early_exit(
            question=q,
            choice=choice,
            config=config,
            t0=t0,
            msg=_reject_message(choice),
            stop_reason="corpus_rejected",
            error=choice.reason,
            extra={"corpus_pin": pin.to_dict()},
        )

    if not llm.api_key and not config.skip_answer:
        raise RuntimeError(
            "未配置 API key：在 RequestConfig.api_key / env_file / DEEPSEEK_API_KEY 中提供"
        )

    # —— 两方对比 ——
    if choice.is_compare:
        return _run_compare_request(
            q,
            config,
            choice,
            llm,
            t0,
            on_progress=on_progress,
            corpus_pin=pin,
        )

    # —— 单方 / 整池 ——
    try:
        active_game = resolve_game_root(
            choice,
            vanilla_root=config.game_root,
            mods_root=config.resolved_mods_root,
        )
    except ValueError as e:
        raise RuntimeError(str(e)) from e

    pool_roots = resolve_pool_mod_roots(
        choice, mods_root=config.resolved_mods_root
    )

    if not active_game.is_dir():
        hint = (
            f"资料库目录不存在：{active_game}。"
            if choice.kind == "mod"
            else f"原版 game_root 不存在：{active_game}。"
        )
        if choice.kind == "mod" and choice.mod:
            hint += f" 请先构建：python3 ingest/build_reduced_mod/build_reduced_mod.py --id {choice.mod.id}"
        msg = f"{choice.banner_line()}\n\n{hint}"
        return _early_exit(
            question=q,
            choice=choice,
            config=config,
            t0=t0,
            msg=msg,
            stop_reason="corpus_missing",
            error=str(active_game),
            extra={"active_game": str(active_game), "corpus_pin": pin.to_dict()},
        )

    if choice.kind == "pool" and not pool_roots:
        msg = (
            f"{choice.banner_line()}\n\n"
            "当前类别目录里有模组条目，但本机尚未构建对应削减树（data/mods/<id>）。"
            "请先构建后再问，或改选手动标签。"
        )
        return _early_exit(
            question=q,
            choice=choice,
            config=config,
            t0=t0,
            msg=msg,
            stop_reason="corpus_missing",
            error="pool_roots_empty",
            extra={"corpus_pin": pin.to_dict(), "pool_size": len(choice.pool)},
        )

    active_loc = config.loc_db_for(active_game)
    snap = config.to_dict()
    snap["active_game_root"] = str(active_game)
    snap["active_loc_db"] = str(active_loc)
    snap["corpus"] = choice.to_dict()
    snap["corpus_pin"] = pin.to_dict()
    if pool_roots:
        snap["pool_mounted"] = len(pool_roots)

    req_log = RequestLogSession.create(
        config.log_dir,
        question=q,
        config_snapshot=snap,
    )

    find_res = _run_find_once(
        question=q,
        game_root=active_game,
        loc_db=active_loc,
        llm=llm,
        config=config,
        corpus_note=_corpus_note(choice),
        req_log=req_log,
        on_progress=on_progress,
        mods_roots=pool_roots or None,
    )

    ans: AnswerResult | None = None
    if config.skip_answer:
        answer_text = json.dumps(find_res.package.to_dict(), ensure_ascii=False, indent=2)
    else:
        if on_progress is not None:
            try:
                on_progress({"phase": "answer", "round": 0, "max_rounds": config.max_rounds})
            except Exception:
                log.debug("on_progress(answer) failed", exc_info=True)
        ans = synthesize_answer(
            package=find_res.package,
            lang=config.lang,
            loc_db=active_loc,
            llm_config=llm,
            log_dir=config.answer_log_dir if config.legacy_side_logs else None,
            request_log=req_log,
        )
        answer_text = ans.answer

    answer_text = _prefix_banner(answer_text, choice)

    elapsed = time.monotonic() - t0
    ok = bool(find_res.ok and (ans.ok if ans else True))
    summary_path = req_log.finalize(
        ok=ok,
        answer=answer_text,
        elapsed_sec=elapsed,
        extra={"corpus": choice.to_dict(), "active_game_root": str(active_game)},
    )

    return RequestResult(
        ok=ok,
        question=q,
        answer=answer_text,
        find=find_res,
        answer_result=ans,
        elapsed_sec=elapsed,
        log_path=summary_path,
        request_id=req_log.request_id,
        log_dir=str(req_log.dir) if config.log_dir is not None else None,
        config_snapshot=snap,
        corpus=choice,
    )


def _run_compare_request(
    question: str,
    config: RequestConfig,
    choice: CorpusChoice,
    llm: LLMConfig,
    t0: float,
    *,
    on_progress: Any = None,
    corpus_pin: CorpusPin | None = None,
) -> RequestResult:
    assert choice.is_compare and len(choice.sides) == 2
    side_a, side_b = choice.sides
    label_a, label_b = side_a.label(), side_b.label()

    try:
        root_a = resolve_side_root(
            side_a,
            vanilla_root=config.game_root,
            mods_root=config.resolved_mods_root,
        )
        root_b = resolve_side_root(
            side_b,
            vanilla_root=config.game_root,
            mods_root=config.resolved_mods_root,
        )
    except ValueError as e:
        raise RuntimeError(str(e)) from e

    missing: list[str] = []
    if not root_a.is_dir():
        missing.append(f"{label_a} → {root_a}")
    if not root_b.is_dir():
        missing.append(f"{label_b} → {root_b}")
    if missing:
        hint = "对比资料库目录缺失：\n" + "\n".join(f"· {m}" for m in missing)
        if side_a.kind == "mod" and side_a.mod:
            hint += f"\n可构建：python3 ingest/build_reduced_mod/build_reduced_mod.py --id {side_a.mod.id}"
        if side_b.kind == "mod" and side_b.mod:
            hint += f"\n可构建：python3 ingest/build_reduced_mod/build_reduced_mod.py --id {side_b.mod.id}"
        msg = f"{choice.banner_line()}\n\n{hint}"
        return _early_exit(
            question=question,
            choice=choice,
            config=config,
            t0=t0,
            msg=msg,
            stop_reason="corpus_missing",
            error=";".join(missing),
            extra={
                "missing": missing,
                "corpus_pin": (corpus_pin or CorpusPin()).to_dict(),
            },
        )

    loc_a = config.loc_db_for(root_a)
    loc_b = config.loc_db_for(root_b)

    snap = config.to_dict()
    snap["corpus"] = choice.to_dict()
    snap["corpus_pin"] = (corpus_pin or CorpusPin()).to_dict()
    snap["compare_roots"] = {
        "a": {"label": label_a, "game_root": str(root_a), "loc_db": str(loc_a)},
        "b": {"label": label_b, "game_root": str(root_b), "loc_db": str(loc_b)},
    }

    req_log = RequestLogSession.create(
        config.log_dir,
        question=question,
        config_snapshot=snap,
    )

    # 各方独立找；不把 request_log 传给 find，避免互相覆盖 LLM 轮次文件
    deixis = (
        [side_a.label(), side_b.label()]
        if choice.reason == "ui_tags_compare"
        else None
    )
    find_a = _run_find_once(
        question=question,
        game_root=root_a,
        loc_db=loc_a,
        llm=llm,
        config=config,
        corpus_note=_side_note(side_a, deixis_labels=deixis),
        req_log=None,
        on_progress=on_progress,
        progress_side_index=0,
        progress_side_count=2,
        progress_side_label=label_a,
    )
    req_log.write_find_detail(
        {**_find_detail_payload(find_a), "side_label": label_a},
        side="a",
    )

    find_b = _run_find_once(
        question=question,
        game_root=root_b,
        loc_db=loc_b,
        llm=llm,
        config=config,
        corpus_note=_side_note(side_b, deixis_labels=deixis),
        req_log=None,
        on_progress=on_progress,
        progress_side_index=1,
        progress_side_count=2,
        progress_side_label=label_b,
    )
    req_log.write_find_detail(
        {**_find_detail_payload(find_b), "side_label": label_b},
        side="b",
    )

    merged_pkg = _merge_compare_packages(
        find_a.package,
        find_b.package,
        label_a=label_a,
        label_b=label_b,
        question=question,
    )
    merged_find = FindResult(
        ok=bool(find_a.ok or find_b.ok),
        package=merged_pkg,
        question=question,
        rounds=int(find_a.rounds) + int(find_b.rounds),
        tool_calls=int(find_a.tool_calls) + int(find_b.tool_calls),
        elapsed_sec=float(find_a.elapsed_sec) + float(find_b.elapsed_sec),
        stop_reason="compare",
        error=find_a.error or find_b.error,
    )
    req_log.write_find_detail(
        {
            **_find_detail_payload(merged_find),
            "merged_coverage": merged_pkg.coverage,
            "sides": {"a": label_a, "b": label_b},
        }
    )

    ans: AnswerResult | None = None
    if config.skip_answer:
        answer_text = json.dumps(merged_pkg.to_dict(), ensure_ascii=False, indent=2)
    else:
        if on_progress is not None:
            try:
                on_progress({"phase": "answer", "round": 0, "max_rounds": config.max_rounds})
            except Exception:
                log.debug("on_progress(answer) failed", exc_info=True)
        ans = synthesize_answer(
            package=merged_pkg,
            lang=config.lang,
            loc_db=loc_a,
            loc_dbs=[loc_a, loc_b],
            llm_config=llm,
            log_dir=config.answer_log_dir if config.legacy_side_logs else None,
            request_log=req_log,
        )
        answer_text = ans.answer

    answer_text = _prefix_banner(answer_text, choice)

    elapsed = time.monotonic() - t0
    ok = bool(merged_find.ok and (ans.ok if ans else True))
    summary_path = req_log.finalize(
        ok=ok,
        answer=answer_text,
        elapsed_sec=elapsed,
        extra={
            "corpus": choice.to_dict(),
            "compare": True,
            "compare_roots": snap["compare_roots"],
        },
    )

    return RequestResult(
        ok=ok,
        question=question,
        answer=answer_text,
        find=merged_find,
        answer_result=ans,
        elapsed_sec=elapsed,
        log_path=summary_path,
        request_id=req_log.request_id,
        log_dir=str(req_log.dir) if config.log_dir is not None else None,
        config_snapshot=snap,
        corpus=choice,
    )


class RequestEngine:
    """可复用配置的薄封装。每次 ask 仍无状态。"""

    def __init__(self, config: RequestConfig):
        self.config = config

    def ask(
        self,
        question: str,
        *,
        corpus_pin: CorpusPin | dict | None = None,
        **overrides,
    ) -> RequestResult:
        cfg = self.config.with_overrides(**overrides) if overrides else self.config
        return run_request(question, cfg, corpus_pin=corpus_pin)
