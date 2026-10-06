import re
import fnmatch
from pathlib import Path
from typing import Any

from mokioclaw.core.state import RuntimeState
from mokioclaw.tools.file_tools import resolve_workspace_path, display_path, read_text_lossy

SKIP_DIRS = {'.git', '.mokioclaw', '.venv','__pycache__', '.pytest_cache'}


def _iter_files(root: Path, glob_pattern:str|None):
    """
    于递归查找目录下符合条件的所有文件，并返回文件路径列表。
    :param root:
    :param glob_pattern:
    :return:
    """
    files : list[Path] = []
    for path in root.glob("*"):
        if not path.is_file():
            continue
        if any(path in SKIP_DIRS for path in path.parts):
            continue
        if (glob_pattern and not
            fnmatch.fnmatch(str(path.name), glob_pattern) and not
            fnmatch.fnmatch(str(path), glob_pattern)):
            continue
        files.append(path)
    return files

def grep(
        state: RuntimeState,
        pattern:str|None,
        path: str='.',
        glob:str | None=None,
        head_limit:int | str = 50,
        ignore_case :bool=False):
    """
    在工作区文件中搜索正则表达式，并返回匹配的文件、行号和内容。
    :param state:
    :param pattern:
    :param path:
    :param glob:
    :param head_limit:
    :param ignore_case:
    :return:
    """
    if not pattern:
        return {
            'ok':False,
            'error':"pattern must not be empty"
        }
    try:
        head_limit_value = int(head_limit)
    except ValueError:
        return {
            'ok':False,
            'error':"head_limit must be an integer"
        }
    if head_limit_value <= 0:
        return {
            'ok':False,
            'error':"head_limit must be > 0"
        }

    root = resolve_workspace_path(state, path)
    if root.is_file():
        candidates = [root]
    elif root.is_dir():
        candidates = _iter_files(root, glob)
    else:
        return {
            'ok':False,
            'error':f"path does not exist: {display_path(state, root)}"
        }
    flags = re.IGNORECASE if ignore_case else 0 # 如果
    try:
        regex = re.compile(pattern, flags=flags)
    except re.error as exc:
        return {
            'ok':False,
            'error':f"invalid regex: {exc}"
        }
    matches: list[dict[str, Any]] = []
    for file in candidates:
        line = read_text_lossy(file).splitlines()
        for idx, line in enumerate(line,start=1):
            if regex.search(line):
                matches.append({
                    'path':display_path(state, file),
                    'line':idx,
                    'text':line,
                })
                if len(matches) >= head_limit_value:
                    return {
                        'ok':True,
                        'pattern':pattern,
                        'matches':matches,
                        'truncated':True
                    }

    return {
        'ok': True,
        'pattern': pattern,
        'matches': matches,
        'truncated': False
    }