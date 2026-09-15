"""bwrap 沙箱：每个 Agent 一个可写工作区 + 只读挂载 game。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SESSIONS_ROOT = Path(__file__).resolve().parent / "sessions"
DEFAULT_GAME = Path(__file__).resolve().parents[1] / "data" / "game"

# 截断：避免单次工具结果撑爆上下文
DEFAULT_STDOUT_LIMIT = 24_000
DEFAULT_STDERR_LIMIT = 8_000


@dataclass
class RunResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    elapsed_sec: float = 0.0
    workdir: str = ""
    truncated: bool = False
    error: str | None = None


@dataclass
class SandboxSession:
    """一次找任务绑定一个沙箱会话；结束后 destroy。"""

    session_id: str
    workdir: Path
    game_root: Path
    sessions_root: Path = field(default_factory=lambda: DEFAULT_SESSIONS_ROOT)
    python_bin: str = "/usr/bin/python3"
    # pool：host 上的 _mods_view（内含 id→削减树 symlink），bwrap 挂到 /mods
    mods_view: Path | None = None
    _alive: bool = field(default=True, repr=False)

    @classmethod
    def create(
        cls,
        *,
        game_root: Path | None = None,
        sessions_root: Path | None = None,
        session_id: str | None = None,
        mods_roots: dict[str, Path] | None = None,
    ) -> SandboxSession:
        sid = session_id or uuid.uuid4().hex[:12]
        root = Path(sessions_root or DEFAULT_SESSIONS_ROOT).resolve()
        root.mkdir(parents=True, exist_ok=True)
        work = root / sid
        work.mkdir(parents=True, exist_ok=False)
        (work / "out").mkdir(exist_ok=True)
        game = Path(game_root or DEFAULT_GAME).resolve()
        if not game.is_dir():
            raise FileNotFoundError(f"game_root missing: {game}")
        mods_view: Path | None = None
        if mods_roots:
            mods_view = work / "_mods_view"
            mods_view.mkdir(exist_ok=True)
            for mid, src in mods_roots.items():
                mid_s = str(mid).strip()
                if not mid_s:
                    continue
                target = Path(src).resolve()
                if not target.is_dir():
                    continue
                link = mods_view / mid_s
                if not link.exists():
                    link.symlink_to(target)
        return cls(
            session_id=sid,
            workdir=work,
            game_root=game,
            sessions_root=root,
            # bwrap 只绑定 /usr /bin 等；venv 路径在沙箱内不可见
            python_bin="/usr/bin/python3",
            mods_view=mods_view,
        )

    def destroy(self) -> None:
        if not self._alive:
            return
        self._alive = False
        # 软删除：移到 sessions_root/_trash，避免永久 rm
        trash = Path(self.sessions_root) / "_trash"
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / f"{self.session_id}_{int(time.time())}"
        try:
            self.workdir.rename(dest)
        except OSError:
            # 回退：清空标记文件，目录留着
            marker = self.workdir / ".destroyed"
            try:
                marker.write_text("1", encoding="utf-8")
            except OSError:
                pass

    def run_code(
        self,
        code: str,
        *,
        timeout_sec: float = 8.0,
        filename: str = "main.py",
        stdout_limit: int = DEFAULT_STDOUT_LIMIT,
        stderr_limit: int = DEFAULT_STDERR_LIMIT,
    ) -> RunResult:
        if not self._alive:
            return RunResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="",
                error="sandbox_destroyed",
                workdir=str(self.workdir),
            )
        if not code or not str(code).strip():
            return RunResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="",
                error="empty_code",
                workdir=str(self.workdir),
            )
        timeout_sec = max(0.5, min(float(timeout_sec), 30.0))
        script = self.workdir / filename
        script.write_text(textwrap.dedent(code), encoding="utf-8")

        bwrap = shutil.which("bwrap")
        t0 = time.monotonic()
        if bwrap:
            cmd = self._bwrap_cmd(script_name=filename)
        else:
            # 无 bwrap：仍隔离 cwd + 禁网尽力；弱于正式沙箱
            cmd = [self.python_bin, str(script)]

        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/work",
            "PYTHONPATH": "",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.workdir) if not bwrap else None,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            stdout, trunc_o = _truncate(proc.stdout or "", stdout_limit)
            stderr, trunc_e = _truncate(proc.stderr or "", stderr_limit)
            return RunResult(
                ok=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
                elapsed_sec=time.monotonic() - t0,
                workdir=str(self.workdir),
                truncated=trunc_o or trunc_e,
            )
        except subprocess.TimeoutExpired as e:
            stdout, trunc_o = _truncate((e.stdout or "") if isinstance(e.stdout, str) else "", stdout_limit)
            stderr, trunc_e = _truncate((e.stderr or "") if isinstance(e.stderr, str) else "", stderr_limit)
            return RunResult(
                ok=False,
                exit_code=None,
                stdout=stdout,
                stderr=stderr or "timeout",
                timed_out=True,
                elapsed_sec=time.monotonic() - t0,
                workdir=str(self.workdir),
                truncated=trunc_o or trunc_e,
                error="timeout",
            )
        except OSError as e:
            return RunResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="",
                elapsed_sec=time.monotonic() - t0,
                workdir=str(self.workdir),
                error=f"os_error: {e}",
            )

    def _bwrap_cmd(self, *, script_name: str) -> list[str]:
        game = str(self.game_root)
        work = str(self.workdir)
        # 最小只读根 + 可写 /work + 只读 /game；禁网
        binds: list[str] = [
            "bwrap",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/usr/local",
            "/usr/local",
            "--ro-bind",
            game,
            "/game",
        ]
        if self.mods_view is not None and self.mods_view.is_dir():
            binds.extend(["--ro-bind", str(self.mods_view.resolve()), "/mods"])
        binds.extend(
            [
                "--bind",
                work,
                "/work",
                "--chdir",
                "/work",
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--tmpfs",
                "/tmp",
                "--unshare-net",
                "--unshare-pid",
                "--die-with-parent",
                "--new-session",
                "--clearenv",
                "--setenv",
                "HOME",
                "/work",
                "--setenv",
                "PATH",
                "/usr/local/bin:/usr/bin:/bin",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "PYTHONDONTWRITEBYTECODE",
                "1",
                "--setenv",
                "PATCHOULI_GAME",
                "/game",
            ]
        )
        if self.mods_view is not None and self.mods_view.is_dir():
            binds.extend(["--setenv", "PATCHOULI_MODS", "/mods"])
        binds.extend(
            [
                self.python_bin,
                f"/work/{script_name}",
            ]
        )
        # 部分发行版 lib64 是真实目录
        if Path("/lib64").is_dir() and not Path("/lib64").is_symlink():
            # 替换 symlink 为 ro-bind
            out: list[str] = []
            i = 0
            while i < len(binds):
                if binds[i] == "--symlink" and i + 2 < len(binds) and binds[i + 2] == "/lib64":
                    out.extend(["--ro-bind", "/lib64", "/lib64"])
                    i += 3
                    continue
                out.append(binds[i])
                i += 1
            binds = out
        return binds


def _truncate(s: str, limit: int) -> tuple[str, bool]:
    if len(s) <= limit:
        return s, False
    return s[:limit] + f"\n…[truncated {len(s) - limit} chars]", True
