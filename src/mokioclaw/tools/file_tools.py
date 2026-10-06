import difflib
from pathlib import Path

from mokioclaw.core.state import RuntimeState
MAX_READ_LINES = 2000
TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gbk")

def _strip_workplace_prefix(file_path: str):
    """
    清理文件路径开头多余的 workspace/ 前缀，最后返回规范化后的路径。
    :param file_path:
    :return:
    """
    normalized = file_path.replace('\\', '/').strip()
    while normalized in {'workplace','./workplace'} or normalized.startswith(('workplace/','./workplace/')):
        if normalized in {'workplace','./workplace'}:
            normalized = '.'
        elif normalized.startswith('./workplace/'):
            normalized = normalized[len('./workplace/'):]
        else:
            normalized = normalized[len('./workplace/'):]
    return normalized


def read_text_lossy(path:Path):
    """
    把文件读取成文本，即使文件不是标准 UTF-8 编码，也尽量不让程序报错。
    :param path:
    :return:
    """
    last_error:UnicodeDecodeError | None = None
    for encoding in TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        return path.read_text(encoding='utf8', errors='replace')
    return path.read_text(encoding='utf8')


def resolve_workspace_path(state: RuntimeState, path: str):
    """
    把用户提供的文件路径转换成一个安全的绝对路径，并确保它位于当前工作区内。
    :param state:
    :param path:
    :return:
    """
    raw = Path(_strip_workplace_prefix(path)).expanduser()
    if not raw.is_absolute():
        raw = state.workspace / raw
    return state.assert_workspace_path(raw)

def display_path(state: RuntimeState, path: Path):
    try:
        return str(path.resolve().relative_to(state.workspace.resolve()))
    except ValueError:
        return str(path)


def read_file(state: RuntimeState, file_path: str, offset: int = 0, limit : int = MAX_READ_LINES):
    path = resolve_workspace_path(state, file_path)
    if not path.exists():
        return {
            'ok': False,
            'error':f"file does not exist: {display_path(state, path)}"
        }
    if not path.is_file():
        return {
            'ok': False,
            'error':f"path is not a file: {display_path(state, path)}"
        }
    try:
        offset_value = int(offset)
        limit_value = int(limit)
    except (TypeError,ValueError):
        return {
            'ok': False,
            'error':f"offset is not an integer"
        }
    if offset_value < 0 or limit_value < 0:
        return {
            'ok': False,
            'error':f"offset and limit values must be positive integer"
        }
    text = read_text_lossy(path)
    lines = text.splitlines()
    limit_value = min(MAX_READ_LINES, limit_value)
    selected = lines[offset_value:offset_value + limit_value]
    complete = offset_value==0 and len(selected) == len(lines)
    state.record_read(path, complete=complete)
    numbered = '\n'.join(f'{offset_value + idx + 1}:{line} ' for idx, line in enumerate(selected))
    return {
        'ok': True,
        'path': display_path(state, path),
        'tota_lines': len(lines),
        'offset': offset_value,
        'limit': limit_value,
        'complete': complete,
        'content': numbered,
    }


def write_file(state: RuntimeState, file_path: str, data: str):
    path = resolve_workspace_path(state, file_path)
    exists = path.exists()
    if exists:
        snapshot = state.snapshot_for(path)
        if snapshot is None:
            return {
                'ok': False,
                'error':"file has not been read yet. Read it before overwriting."
            }
        if path.stat().st_mtime_ns != snapshot.mtime_ns:
            return {
                'ok': False,
                'error':"file changed after it was read. Read it again before writing."
            }
        original = read_text_lossy(path)
    else:
        original = ""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding='utf8')
    state.record_read(path, complete=True)
    diff = '\n'.join(
        difflib.unified_diff(
            original.splitlines(),
            data.splitlines(),
            fromfile=f'a/{display_path(state, path)}',
            tofile=f'b/{display_path(state, path)}',
            lineterm='',
        )
    )
    return {
        'ok': True,
        'type':'update' if exists else 'create',
        'path': display_path(state, path),
        'lines': len(data.splitlines()),
        'diff': diff[:4000],
    }


def edit_file(state: RuntimeState, file_path: str, old_text: str, new_text: str):
    path = resolve_workspace_path(state, file_path)

    if not path.exists():
        return {
            'ok': False,
            'error':f"file does not exist: {display_path(state, path)}"
        }
    snapshot = state.snapshot_for(path)
    if snapshot is None:
        return {
            'ok': False,
            'error':f"file has not been read yet. Read it before overwriting."
        }
    if path.stat().st_mtime_ns != snapshot.mtime_ns:
        return {
            'ok': False,
            'error':f"file changed after it was read. Read it again before writing."
        }
    if not old_text:
        return {
            'ok': False,
            'error':'old_text must not be empty'
        }

    original = read_text_lossy(path)
    count = original.count(old_text)
    if count == 0:
        return {
            'ok': False,
            'error':f"old_text was not found"
        }
    if count > 1:
        return {
            'ok': False,
            'error':f"old_text matched {count} times. Provide a unique snippet."
        }

    updated = original.replace(old_text, new_text, 1)
    path.write_text(updated, encoding="utf-8")
    state.record_read(path, complete=True)

    diff = '\n'.join(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f'a/{display_path(state, path)}',
            tofile=f'b/{display_path(state, path)}',
            lineterm='',
        )
    )
    return {
        'ok': True,
        'type':'update',
        'path': display_path(state, path),
        'replacements':1,
        'diff': diff[:4000],
    }
