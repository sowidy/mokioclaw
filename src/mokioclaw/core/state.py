from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    mtime_ns: int
    complete: bool


@dataclass
class RuntimeState:
    workspace: Path
    read_files: dict[Path, FileSnapshot] = field(default_factory=dict)

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
