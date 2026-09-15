"""root_path 约定布局与 init 检查。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REQUIRED_TOP = ("config", "data", "derived", "tools", "rules")


@dataclass(frozen=True)
class RootLayout:
    root: Path
    config_dir: Path
    data_dir: Path
    derived_dir: Path
    tools_dir: Path
    rules_dir: Path
    config_toml: Path
    env_file: Path
    tools_manifest: Path

    @classmethod
    def from_root(cls, root_path: Path | str) -> RootLayout:
        root = Path(root_path).expanduser().resolve()
        config_dir = root / "config"
        return cls(
            root=root,
            config_dir=config_dir,
            data_dir=root / "data",
            derived_dir=root / "derived",
            tools_dir=root / "tools",
            rules_dir=root / "rules",
            config_toml=config_dir / "config.toml",
            env_file=config_dir / ".env",
            tools_manifest=root / "tools" / "manifest.json",
        )


@dataclass
class CheckIssue:
    level: str  # error | warn
    path: str
    message: str


def check_layout(layout: RootLayout) -> list[CheckIssue]:
    """只检查有没有、必要字段能否找到（不做语义正确性）。"""
    issues: list[CheckIssue] = []
    if not layout.root.is_dir():
        issues.append(CheckIssue("error", str(layout.root), "root_path 不是目录"))
        return issues

    for name in REQUIRED_TOP:
        p = layout.root / name
        if not p.is_dir():
            issues.append(CheckIssue("error", str(p), f"缺少目录 {name}/"))
        elif not any(p.iterdir()):
            issues.append(CheckIssue("error", str(p), f"{name}/ 为空"))

    if not layout.config_toml.is_file():
        issues.append(
            CheckIssue("error", str(layout.config_toml), "缺少 config/config.toml")
        )
    if not layout.env_file.is_file():
        issues.append(
            CheckIssue(
                "warn",
                str(layout.env_file),
                "缺少 config/.env（若环境变量已有 API key 可忽略）",
            )
        )
    if not layout.tools_manifest.is_file():
        issues.append(
            CheckIssue("error", str(layout.tools_manifest), "缺少 tools/manifest.json")
        )

    # rules：至少找侧 system + 答侧 system
    for fname in ("find_system.txt", "answer_system.txt"):
        fp = layout.rules_dir / fname
        if not fp.is_file() or fp.stat().st_size == 0:
            issues.append(CheckIssue("error", str(fp), f"缺少或空的 rules/{fname}"))

    # data / derived：至少一个语料子目录
    data_kids = [p for p in layout.data_dir.iterdir() if p.is_dir()] if layout.data_dir.is_dir() else []
    der_kids = (
        [p for p in layout.derived_dir.iterdir() if p.is_dir()]
        if layout.derived_dir.is_dir()
        else []
    )
    if not data_kids:
        issues.append(CheckIssue("error", str(layout.data_dir), "data/ 下无语料子目录"))
    if not der_kids:
        issues.append(
            CheckIssue("error", str(layout.derived_dir), "derived/ 下无语料子目录")
        )

    # 每个 data 子目录若有同名 derived，检查关键衍生文件是否存在
    for d in data_kids:
        der = layout.derived_dir / d.name
        if not der.is_dir():
            issues.append(
                CheckIssue(
                    "warn",
                    str(der),
                    f"data/{d.name} 无对应 derived/{d.name}（invoke 时若列入 constraint 会失败）",
                )
            )
            continue
        for must in (
            "localization.sqlite",
            "common_registry.sqlite",
            "routing_table.json",
        ):
            mp = der / must
            if not mp.is_file() or mp.stat().st_size == 0:
                issues.append(
                    CheckIssue("error", str(mp), f"derived/{d.name}/{must} 缺失或空")
                )

    return issues
