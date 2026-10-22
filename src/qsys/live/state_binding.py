"""Live 独立迁移的一次性稳定路径绑定。

绑定在安装器失败补偿移除组件 drop-in 后仍须保留；它只选择状态位置，不切换代码。
安装器尚未接线时没有 marker，全部现有行为保持。marker 只能由受控安装器创建。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

BINDING_FILE = Path("/etc/qsys/components/live-core/state-binding-v1")
BINDING_CONTENT = b"qsys-live-core-state-v1\n"
STATE_ROOT = Path("/home/ubuntu/qsys/var/live-core")
LEGACY_KILL = Path("/home/ubuntu/qsys/var/KILL")


@dataclass(frozen=True)
class LiveStateBinding:
    root: Path

    @property
    def state(self) -> Path:
        return self.root / "var/live-state.sqlite3"

    @property
    def kill(self) -> Path:
        return self.root / "var/KILL"

    @property
    def workspace(self) -> Path:
        return self.root / "var/live"

    @property
    def lock(self) -> Path:
        return self.root / "var/live-core.lock"


def current_binding() -> LiveStateBinding | None:
    try:
        info = BINDING_FILE.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & 0o022 or info.st_size != len(BINDING_CONTENT)):
        raise ValueError("Live state binding marker ownership/type/mode invalid")
    if BINDING_FILE.read_bytes() != BINDING_CONTENT:
        raise ValueError("Live state binding marker content invalid")
    return LiveStateBinding(STATE_ROOT)


def local_kill_path(configured: Path | None) -> Path | None:
    """每次读取重新解析；预发布的Web无需重启即可观察正式绑定。

    自定义验证路径与未配置保持原语义，不把任意调用者重定向到生产状态。
    """
    if configured not in (Path("var/KILL"), LEGACY_KILL):
        return configured
    binding = current_binding()
    return binding.kill if binding is not None else configured


def state_path(configured: Path) -> Path:
    if configured not in (Path("var/live-state.sqlite3"), Path("/home/ubuntu/qsys/var/live-state.sqlite3")):
        return configured
    binding = current_binding()
    return binding.state if binding is not None else configured


def apply_entry_binding(args) -> None:
    """共享入口失败补偿也读同一最新状态；验证与自定义路径保持显式选择。"""
    if args.validation_namespace is not None:
        return
    binding = current_binding()
    if binding is None:
        return
    for name, legacy, target in (
        ("state", "var/live-state.sqlite3", binding.state),
        ("workspace", "var/live", binding.workspace),
        ("lock", "var/live-core.lock", binding.lock),
        ("kill_switch", "var/KILL", binding.kill),
    ):
        if Path(getattr(args, name)) in (Path(legacy), Path("/home/ubuntu/qsys") / legacy):
            setattr(args, name, str(target))
