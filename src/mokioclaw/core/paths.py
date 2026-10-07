from datetime import datetime
from pathlib import Path
from uuid import uuid4


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()

    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return current

def default_workplace_root(root: Path) -> Path:
    return (root or find_project_root())/ '.mokioclaw' / 'workplace'


def new_task_workplace(root: Path | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid4().hex[:6]
    return default_workplace_root(root) / f"workplace-{stamp}-{suffix}"


def default_workplace(root: Path | None = None) -> Path:
    return new_task_workplace(root)