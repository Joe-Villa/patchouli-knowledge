"""一次请求的全部路径与运行参数。全部由配置给定，不依赖 QQ。"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

_CORE = Path(__file__).resolve().parent.parent
# service/1836 → service → 仓库根；约定：请求日志在 log/1836/
_REPO = _CORE.parent.parent
_DEFAULT_LOG_DIR = _REPO / "log" / "1836"


def _loc_db_usable(path: Path) -> bool:
    """存在且含 localization 表。模组无 loc 源时削减可能不生成库，或留下空壳。"""
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='localization'"
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return False


@dataclass(frozen=True)
class RequestConfig:
    """无状态请求的配置。同配置可复用；每次 ask 互不共享会话状态。"""

    # —— 路径 ——
    game_root: Path
    """削减后的原版 game 根（N=13 纯文本树 + 各 sqlite；loc 仅库）。"""

    sessions_root: Path
    """沙箱会话工作区父目录（每请求新建子目录，结束后软删除）。"""

    log_dir: Path | None = None
    """总日志目录；为 None 则不写盘。子目录：find/ / answer/ / pipeline/。"""

    loc_db: Path | None = None
    """localization.sqlite；默认随选定 corpus 的 game 根。显式指定则禁用自动切换。"""

    env_file: Path | None = None
    """LLM 密钥等 dotenv；不读 QQ 管线。"""

    mods_root: Path | None = None
    """削减后的模组根（其下 <workshop_id>/）；默认 data/mods。"""

    mods_catalog: Path | None = None
    """mods_catalog.json；默认 data/mods_catalog.json。"""

    # —— LLM ——
    api_key: str | None = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    llm_timeout_sec: float = 90.0

    # —— 找侧边界 ——
    max_rounds: int = 12
    submit_grace: int = 3
    max_wall_sec: float = 180.0
    lang: str = "simp_chinese"

    # —— 行为 ——
    skip_answer: bool = False
    """True 时只找，answer 字段为证据包 JSON。"""

    legacy_side_logs: bool = False
    """True 时额外写旧式 find/logs、llm/logs 分散文件（默认只写 reqlog）。"""

    auto_select_corpus: bool = True
    """True：按题干选择原版或某个模组资料库；False：始终用 game_root。"""

    @property
    def resolved_mods_root(self) -> Path:
        if self.mods_root is not None:
            return Path(self.mods_root).resolve()
        return (_CORE / "data" / "mods").resolve()

    @property
    def resolved_mods_catalog(self) -> Path:
        if self.mods_catalog is not None:
            return Path(self.mods_catalog).resolve()
        return (_CORE / "data" / "mods_catalog.json").resolve()

    @property
    def resolved_loc_db(self) -> Path:
        if self.loc_db is not None:
            return Path(self.loc_db).resolve()
        return self.loc_db_for(self.game_root)

    def loc_db_for(self, corpus_game_root: Path) -> Path:
        """选定 corpus 后的 loc；坏库/空壳回退到原版 game_root 下的 sqlite。

        无 localization/ 的模组（如 Kuromi's AI）削减时不生成可用库；
        运行期若仍指向缺失路径，sqlite.connect 会建出 0 字节空文件并炸表不存在。
        显式写死 loc_db 时不做回退。
        """
        if self.loc_db is not None:
            return Path(self.loc_db).resolve()
        cand = (Path(corpus_game_root) / "localization.sqlite").resolve()
        if _loc_db_usable(cand):
            return cand
        fallback = (Path(self.game_root) / "localization.sqlite").resolve()
        if cand != fallback and _loc_db_usable(fallback):
            return fallback
        return cand

    @property
    def find_log_dir(self) -> Path | None:
        return None if self.log_dir is None else Path(self.log_dir) / "find"

    @property
    def answer_log_dir(self) -> Path | None:
        return None if self.log_dir is None else Path(self.log_dir) / "answer"

    @property
    def pipeline_log_dir(self) -> Path | None:
        return None if self.log_dir is None else Path(self.log_dir) / "pipeline"

    @property
    def requests_log_dir(self) -> Path | None:
        """新日志：log_dir/requests/{request_id}/。"""
        return None if self.log_dir is None else Path(self.log_dir) / "requests"

    def with_overrides(self, **kwargs: Any) -> RequestConfig:
        return replace(self, **kwargs)

    @classmethod
    def defaults(cls, *, core: Path | None = None) -> RequestConfig:
        """开发机默认：语料相对服务目录；请求日志约定在仓库 log/1836/。"""
        root = Path(core or _CORE).resolve()
        return cls(
            game_root=root / "data" / "game",
            sessions_root=root / "sandbox" / "sessions",
            log_dir=_DEFAULT_LOG_DIR,
            env_file=root / "llm" / ".env",
            mods_root=root / "data" / "mods",
            mods_catalog=root / "data" / "mods_catalog.json",
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, base_dir: Path | None = None) -> RequestConfig:
        """从 dict 构建。相对路径相对 base_dir（默认 cwd）。"""
        base = Path(base_dir or Path.cwd()).resolve()

        def rel(key: str, default: Any = None) -> Path | None:
            raw = data.get(key, default)
            if raw is None or raw == "":
                return None
            p = Path(os.path.expanduser(str(raw)))
            if not p.is_absolute():
                p = base / p
            return p.resolve()

        game = rel("game_root")
        if game is None:
            raise ValueError("config 需要 game_root")
        sessions = rel("sessions_root")
        if sessions is None:
            raise ValueError("config 需要 sessions_root")

        log_raw = data.get("log_dir", None)
        if log_raw is False or log_raw == "":
            log_dir = None
        elif log_raw is None and "log_dir" not in data:
            log_dir = None
        else:
            log_dir = rel("log_dir")

        auto_raw = data.get("auto_select_corpus")
        auto_select = True if auto_raw is None else bool(auto_raw)

        return cls(
            game_root=game,
            sessions_root=sessions,
            log_dir=log_dir,
            loc_db=rel("loc_db"),
            env_file=rel("env_file"),
            mods_root=rel("mods_root"),
            mods_catalog=rel("mods_catalog"),
            api_key=(str(data["api_key"]).strip() if data.get("api_key") else None),
            base_url=str(data.get("base_url") or "https://api.deepseek.com").rstrip("/"),
            model=str(data.get("model") or "deepseek-chat").strip(),
            llm_timeout_sec=float(data.get("llm_timeout_sec") or 90),
            max_rounds=int(data.get("max_rounds") or 12),
            submit_grace=int(data.get("submit_grace") or 3),
            max_wall_sec=float(data.get("max_wall_sec") or 180),
            lang=str(data.get("lang") or "simp_chinese"),
            skip_answer=bool(data.get("skip_answer") or False),
            legacy_side_logs=bool(data.get("legacy_side_logs") or False),
            auto_select_corpus=auto_select,
        )

    @classmethod
    def from_file(cls, path: Path | str) -> RequestConfig:
        path = Path(path).resolve()
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix == ".toml":
            try:
                import tomllib
            except ImportError:  # py<3.11
                import tomli as tomllib  # type: ignore
            data = tomllib.loads(text)
        elif suffix in (".json",):
            data = json.loads(text)
        else:
            # 无后缀或 .conf：尝试 JSON，再 TOML
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib  # type: ignore
                data = tomllib.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"配置根须为对象: {path}")
        # 允许顶层包一层 request / patchouli
        for key in ("request", "patchouli"):
            inner = data.get(key)
            if isinstance(inner, dict):
                data = inner
                break
        return cls.from_mapping(data, base_dir=path.parent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_root": str(self.game_root),
            "sessions_root": str(self.sessions_root),
            "log_dir": None if self.log_dir is None else str(self.log_dir),
            "loc_db": None if self.loc_db is None else str(self.loc_db),
            "env_file": None if self.env_file is None else str(self.env_file),
            "mods_root": None if self.mods_root is None else str(self.mods_root),
            "mods_catalog": None if self.mods_catalog is None else str(self.mods_catalog),
            "api_key": "***" if self.api_key else None,
            "base_url": self.base_url,
            "model": self.model,
            "llm_timeout_sec": self.llm_timeout_sec,
            "max_rounds": self.max_rounds,
            "submit_grace": self.submit_grace,
            "max_wall_sec": self.max_wall_sec,
            "lang": self.lang,
            "skip_answer": self.skip_answer,
            "legacy_side_logs": self.legacy_side_logs,
            "auto_select_corpus": self.auto_select_corpus,
        }
