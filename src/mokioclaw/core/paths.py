from pathlib import Path

def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()

    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return current

def default_project_root(root: Path) -> Path:
    return (root or find_project_root())/ '.mokioclaw' / 'workplace'

