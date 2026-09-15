"""对外 API：PatchouliQA.init / invoke。"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_engine_config
from .layout import CheckIssue, RootLayout, check_layout
from .rules_loader import build_find_user_prompt, load_rules
from .runtime_view import materialize_views, resolve_constraint_names

log = logging.getLogger("patchouli_qa")

_VENDOR = Path(__file__).resolve().parent / "vendor"


def _ensure_vendor_path() -> None:
    sp = str(_VENDOR)
    if sp not in sys.path:
        sys.path.insert(0, sp)


@dataclass
class InvokeResult:
    ok: bool
    answer: str
    question: str
    request_id: str
    log_dir: str
    coverage: str | None = None
    elapsed_sec: float = 0.0
    stop_reason: str | None = None
    error: str | None = None
    data_constraint: list[str] = field(default_factory=list)
    package: dict[str, Any] | None = None


class PatchouliQA:
    """无状态问答引擎：init 绑定 root；每次 invoke 独立跑完。"""

    def __init__(
        self,
        layout: RootLayout,
        config: EngineConfig,
        rules: Any,
        tools_manifest: dict[str, Any],
        init_issues: list[CheckIssue],
    ) -> None:
        self.layout = layout
        self.config = config
        self.rules = rules
        self.tools_manifest = tools_manifest
        self.init_issues = init_issues

    @classmethod
    def init(cls, root_path: str | Path) -> PatchouliQA:
        layout = RootLayout.from_root(root_path)
        issues = check_layout(layout)
        errors = [i for i in issues if i.level == "error"]
        if errors:
            msg = "; ".join(f"{e.path}: {e.message}" for e in errors)
            raise FileNotFoundError(f"init 检查失败: {msg}")

        config = load_engine_config(layout.config_toml, layout.env_file, layout.root)
        if not (config.api_key or "").strip():
            log.warning("未检测到 API key（config.api_key / DEEPSEEK_API_KEY）")

        rules = load_rules(layout.rules_dir)
        manifest = json.loads(layout.tools_manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest.get("tools"), list) or not manifest["tools"]:
            raise ValueError("tools/manifest.json 需要非空 tools 列表")

        return cls(
            layout=layout,
            config=config,
            rules=rules,
            tools_manifest=manifest,
            init_issues=issues,
        )

    def invoke(
        self,
        input: str,
        data_constraint: list[str],
        output_log: str | Path,
        *,
        skip_answer: bool | None = None,
    ) -> InvokeResult:
        """跑一轮找→答。不保留跨 invoke 记忆。"""
        _ensure_vendor_path()
        from agent import find_evidence
        from answer import synthesize_answer
        from client import LLMConfig
        from reqlog_session import RequestLogSession

        q = (input or "").strip()
        if not q:
            raise ValueError("input 为空")

        names = resolve_constraint_names(self.layout.data_dir, data_constraint)
        out_root = Path(output_log).expanduser().resolve()
        out_root.mkdir(parents=True, exist_ok=True)

        views_root = (
            self.config.sessions_root or (self.layout.root / "sessions")
        ) / "_views"
        views_root.mkdir(parents=True, exist_ok=True)
        mounts = materialize_views(
            data_dir=self.layout.data_dir,
            derived_dir=self.layout.derived_dir,
            names=names,
            views_root=views_root,
        )
        primary = mounts[0]
        mods_roots = {m.name: m.view_root for m in mounts[1:]} or None
        visible = frozenset(mods_roots.keys()) if mods_roots else frozenset()

        loc_db = primary.view_root / "localization.sqlite"
        if not loc_db.is_file():
            raise FileNotFoundError(f"缺少 localization.sqlite: {loc_db}")

        cfg = self.config
        llm = LLMConfig(
            api_key=(cfg.api_key or "").strip(),
            base_url=(cfg.base_url or "https://api.deepseek.com").rstrip("/"),
            model=(cfg.model or "deepseek-chat").strip(),
            timeout_sec=float(cfg.llm_timeout_sec),
        )

        if len(mounts) == 1:
            corpus_note = (
                f"资料库约束：仅可访问「{primary.name}」"
                "（路径相对 /game；不要假设未挂载的模组存在）。"
            )
        else:
            others = ", ".join(m.name for m in mounts[1:])
            corpus_note = (
                f"资料库约束：主树「{primary.name}」挂载为 /game；"
                f"额外语料 {others} 挂载为 /mods/<名>/…。"
                "只读这些路径，禁止访问未列出的语料。"
            )

        request_log = RequestLogSession.create(
            log_root=out_root,
            question=q,
            config_snapshot={
                "data_constraint": names,
                "max_rounds": cfg.max_rounds,
                "model": llm.model,
            },
        )
        request_id = request_log.request_id
        log_dir = request_log.dir

        t0 = time.monotonic()
        find_user = build_find_user_prompt(self.rules, q, corpus_note=corpus_note)

        # 按 tools/manifest.json 过滤引擎内置工具 schema
        from tools import TOOL_SCHEMAS as _ALL_TOOLS

        allowed = {str(x) for x in (self.tools_manifest.get("tools") or [])}
        if allowed:
            tool_schemas = [
                t
                for t in _ALL_TOOLS
                if (t.get("function") or {}).get("name") in allowed
            ]
        else:
            tool_schemas = None

        find_res = find_evidence(
            q,
            game_root=primary.view_root,
            loc_db=loc_db,
            llm_config=llm,
            max_rounds=cfg.max_rounds,
            submit_grace=cfg.submit_grace,
            max_wall_sec=cfg.max_wall_sec,
            log_dir=None,
            lang=cfg.lang,
            sessions_root=cfg.sessions_root,
            request_log=request_log,
            corpus_note=corpus_note,
            mods_roots=mods_roots,
            visible_mod_ids=visible,
            system_prompt=self.rules.find_system,
            user_prompt=find_user,
            tool_schemas=tool_schemas,
        )

        do_skip_answer = cfg.skip_answer if skip_answer is None else skip_answer
        answer_text = ""
        answer_err = None
        if do_skip_answer:
            answer_text = (
                f"[skip_answer] coverage={find_res.package.coverage} "
                f"items={len(find_res.package.items)}"
            )
        else:
            ans = synthesize_answer(
                question=q,
                package=find_res.package,
                lang=cfg.lang,
                loc_db=loc_db,
                llm_config=llm,
                log_dir=None,
                request_log=request_log,
                system_prompt=self.rules.answer_system,
            )
            answer_text = ans.answer
            answer_err = ans.error

        elapsed = time.monotonic() - t0
        if do_skip_answer:
            ok = bool(find_res.ok)
        else:
            ok = bool(find_res.ok) and not answer_err

        try:
            request_log.finalize(
                ok=ok,
                answer=answer_text,
                elapsed_sec=elapsed,
                extra={
                    "coverage": find_res.package.coverage,
                    "stop_reason": find_res.stop_reason,
                    "data_constraint": names,
                    "package": find_res.package.to_dict(),
                    "find_error": find_res.error,
                    "answer_error": answer_err,
                },
            )
        except Exception:
            log.debug("finalize log failed", exc_info=True)

        return InvokeResult(
            ok=ok,
            answer=answer_text,
            question=q,
            request_id=request_id,
            log_dir=str(log_dir),
            coverage=find_res.package.coverage,
            elapsed_sec=elapsed,
            stop_reason=find_res.stop_reason,
            error=answer_err or find_res.error,
            data_constraint=names,
            package=find_res.package.to_dict(),
        )
