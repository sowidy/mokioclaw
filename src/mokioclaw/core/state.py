from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mokioclaw.core.approval import ApprovalRequest, ApprovalDecision, normalize_approval_mode


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    mtime_ns: int
    complete: bool


@dataclass
class RuntimeState:
    workspace: Path
    read_files: dict[Path, FileSnapshot] = field(default_factory=dict)
    approval_mode: str = "inline"
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision | bool] | None = None # 一个接收 ApprovalRequest 参数，并返回 ApprovalDecision 或 bool 的可调用对象。
    bash_default_timeout_seconds: int = 120
    bash_max_timeout_seconds: int = 600
    bash_max_output_chars: int = 6000
    bash_env_file: Path | None = None

    def __post_init__(self) -> None:
        """
        __init__ 执行完后自动调用的方法，常用于初始化后的校验或数据规范化。
        :return:
        """
        self.approval_mode = normalize_approval_mode(self.approval_mode)

    def assert_workspace_path(self, path: Path) -> Path:
        resolved = path.resolve()
        workspace = self.workspace.resolve()
        if resolved != workspace and workspace not in resolved.parents:
            raise ValueError(f"path must stay inside workspace: {workspace}")
        return resolved

    def record_read(self, path: Path, *, complete: bool) -> None:
        resolved = self.assert_workspace_path(path)
        stat = resolved.stat()
        self.read_files[resolved] = FileSnapshot(
            path=resolved,
            mtime_ns=stat.st_mtime_ns,
            complete=complete,
        )

    def snapshot_for(self, path: Path) -> FileSnapshot | None:
        return self.read_files.get(path.resolve())
