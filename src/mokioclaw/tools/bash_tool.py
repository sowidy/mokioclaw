import os
import platform
import re
import shlex #
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mokioclaw.core.approval import classify_command_risk, make_approval_request, ApprovalDecision
from mokioclaw.core.state import RuntimeState

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_TIMEOUT_SECONDS = 600
DEFAULT_MAX_OUTPUT_CHARS = 6000

DANGEROUS_PATTERNS = {
    r"\brm\s+-rf\b",
    r"\bRemove-Item\b.*\b-Recurse\b.*\b-Force\b",
    r"\bdel\s+/[sq]\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"(?:^|[^0-9])>\s*(?:[A-Za-z]:\\|/(?!dev/null\b))",
}

def bash_tool_description() -> str:
    system = platform.system().lower()
    common = (
        "Run a safe development shell command inside the workspace with timeout and output capture. "
        "The command already runs with cwd set to the workspace, so use relative paths and do not run cd /workspace, "
        "cd workspace, or long-lived interactive commands.Each call starts a fresh shell; exported variables do not persist "
        "between calls, so write reusable environment values to the configured env file or pass them inline. "
        "Long-running servers should use run_in_background=true.Prefer cross-platform Python one-liners for file checks."
    )
    if system == "windows":
        return (
            common
            + " Current platform: Windows. Commands are executed by cmd.exe, not bash or PowerShell. "
            "Use Windows cmd syntax: dir for listing, type file.txt for printing a file, copy/move/del for simple file operations, "
            "&& for chaining, and set VAR=value for environment variables. Do not use POSIX-only tools like tail, grep, sed, awk, "
            "cat, ls, export, or here-documents unless you implement the behavior with python -c."
        )
    if system == "darwin":
        return (
            common
            + " Current platform: macOS. Commands are executed by a POSIX shell. "
            "Use portable sh/bash-style commands such as ls, cat, grep, tail, export, and python/python3 as available."
        )
    return (
        common
        + " Current platform: Linux/Unix. Commands are executed by a POSIX shell. "
        "Use portable sh/bash-style commands such as ls, cat, grep, tail, export, and python/python3 as available."
    )


def _coerce_timeout(timeout_seconds: int | float | str):
    """
    规范时间为int或DEFAULT
    :param timeout_seconds:
    :return:
    """
    if timeout_seconds is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return int(timeout_seconds)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def _normalize_command(command: str) -> str:
    """
    根据当前操作系统，把 Agent 生成的命令转换成更适合当前环境执行的形式。
    :param command:
    :return:
    """
    if os.name == "nt": # Windows 分支
        normalized = re.sub(r"^\s*python3(\.exe)?\b", "python", command, count=1,flags=re.IGNORECASE)
        normalized = re.sub(r"\bls\s+-la\b", "dir", normalized)
        normalized = re.sub(r"\bls\b", "dir", normalized)
        normalized = re.sub(r"\bcat\s+([^\s|&<>]+)", r"type \1", normalized)
        return normalized
    return re.sub(
        r"^\s*cd\s+(?:/workplace|workplace|\.?/workplace|\.mokioclaw[\\/]+workplace)\s*(?:&&|;)\s*pwd\s*$",
        "cd",
        command,
        count=1,
        flags=re.IGNORECASE,
    )


def _handle_tail_command(state: RuntimeState, command: str):
    """
    识别并直接处理简单的 tail 命令，读取文件末尾若干行，避免交给系统 shell 执行。
    :param state:
    :param command:
    :return:
    """
    match = re.fullmatch(r"\s*tail(?:\s+-n)?\s+(\d+)\s+(.+?)\s*", command)
    if not match:
        match = re.fullmatch(r"\s*tail\s+-(\d+)\s+(.+?)\s*", command)
    if not match:
        return None
    count = int(match.group(1)) # tail 后第一个参数：行数
    raw_path = shlex.split(match.group(2), posix=False)[0] # 文件路径
    from mokioclaw.tools.file_tools import read_text_lossy, resolve_workspace_path
    path = resolve_workspace_path(state, raw_path)
    if not path.exists() or not path.is_file():
        return {
            'ok': False,
            'error':f"file does not exist: {raw_path}"
        }
    lines = read_text_lossy(path).splitlines()
    output = '\n'.join(lines[-count:])
    return {
        "ok": True,
        "timed_out": False,
        "command": command,
        "exit_code": 0,
        "stdout": output + ("\n" if output else ""),
        "stderr": "",
        "duration_ms": 0,
    }


def _looks_dangerous(command:str):
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return pattern
    return None

def _decode_output(output: bytes | str | None) -> str:
    """
    以不同解码方式正确获取文本
    :param output:
    :return:
    """
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            return output.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return output.decode("utf-8", errors="replace")

def _resolve_approval(state: RuntimeState, command: str) -> dict[str, Any] | None:
    risk_reason = classify_command_risk(command)
    if risk_reason is None:
        return None

    request = make_approval_request(command, risk_reason)
    base = {
        "requires_approval": True,
        "approval_id": request.id,
        "risk_reason": risk_reason,
        "command": command,
    }
    if state.approval_mode == "auto":
        return {**base, "approved": True}
    if state.approval_mode == "deny" or state.approval_handler is None:
        return {
            **base,
            "ok": False,
            "approved": False,
            "error": f"human approval required for high-risk command: {risk_reason}",
        }

    decision = state.approval_handler(request) # 获取人的决策
    if isinstance(decision, ApprovalDecision):
        approved = decision.approved
        decision_reason = decision.reason
    else:
        approved = bool(decision)
        decision_reason = ""
    if approved:
        return {**base, "approved": True}
    return {
        **base,
        "ok": False,
        "approved": False,
        "error": decision_reason or f"human rejected high-risk command: {risk_reason}",
    }


def run_bash(
    state: RuntimeState,
    command: str,
    timeout_seconds: int | str | float | None = None,
    run_in_background: bool | str = False,
) -> dict[str, Any]:
    if not command.strip():
        return {
            'ok': False,
            'error': "command is empty",
        }
    max_timeout = _state_int(state, "bash_max_timeout_seconds", DEFAULT_MAX_TIMEOUT_SECONDS)
    timeout = _coerce_timeout(timeout_seconds)
    if timeout_seconds is None:
        timeout = _state_int(state, "bash_default_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if timeout <= 0 or timeout > max_timeout:
        return {"ok": False, "error": f"timeout_seconds must be between 1 and {max_timeout}"}
    normalized_command = _normalize_command(command)
    background = _coerce_bool(run_in_background)
    handled = _handle_tail_command(state, normalized_command)
    if handled is not None:
        return handled
    block = _looks_dangerous(normalized_command)
    if block:
        return {
            'ok': True,
            'error': f"blocked potentially dangerous command pattern: {block}",
        }
    approval = _resolve_approval(state, normalized_command) # 人的决策
    if approval is not None and not approval.get("approved"):
        return approval
    started = time.perf_counter()
    env, env_error = _build_env(state)
    if env_error is not None:
        return {"ok": False, "error": env_error}
    max_output_chars = _state_int(state, "bash_max_output_chars", DEFAULT_MAX_OUTPUT_CHARS)
    if background:
        return _run_background(state, normalized_command, env, approval)

    try:
        completed = subprocess.run(
            normalized_command,
            cwd=state.workplace,
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            'ok': False,
            'timed_out': True,
            'exit_code': None,
            **_format_captured_output(state, _decode_output(exc.stdout), _decode_output(exc.stderr), max_output_chars),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            **(approval or {}),
        }

    output = _format_captured_output(state, _decode_output(completed.stdout), _decode_output(completed.stderr), max_output_chars)
    return {
        'ok': completed.returncode == 0,
        'timed_out': False,
        'command': normalized_command,
        'exit_code': completed.returncode,
        **output,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        **(approval or {})
    }


def _state_int(state: RuntimeState, name: str, default: int) -> int:
    """
    转int
    :param state:
    :param name:
    :param default:
    :return:
    """
    try:
        value = int(getattr(state, name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _coerce_bool(value: bool | str) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_env(state: RuntimeState) -> tuple[dict[str, str], str | None]:
    """
    基于当前 Python 进程环境进行扩展
    :param state:
    :return:
    """
    env = os.environ.copy() # 复制当前进程环境，因此会继承当前进程中的变量
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    _prepend_harness_paths(state, env)
    env_file = state.bash_env_file or state.workplace / ".mokioclaw.env"
    if env_file.exists():
        try:
            env.update(_parse_env_file(env_file, env)) # 会覆盖前面已有的变量
        except OSError as exc:
            return env, f"failed to read bash env file {env_file}: {exc}"
    return env, None


def _prepend_harness_paths(state: RuntimeState, env: dict[str, str]) -> None:
    """
    把一些重要目录放到 PATH 的最前面，确保执行命令时优先使用项目或当前 Python 环境中的工具。
    :param state:
    :param env:
    :return:
    """
    path_candidates = [
        _ensure_toolchain_shims(state),
        state.workplace / ".venv" / ("Scripts" if os.name == "nt" else "bin"),
        state.workplace / "venv" / ("Scripts" if os.name == "nt" else "bin"),
        state.workplace / "node_modules" / ".bin",
        Path(sys.executable).parent,
    ]
    existing = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    merged: list[str] = []
    for path in [str(candidate) for candidate in path_candidates if candidate.exists()] + existing:
        if path not in merged:
            merged.append(path)
    env["PATH"] = os.pathsep.join(merged)
    if getattr(sys, "prefix", None) and sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        env.setdefault("VIRTUAL_ENV", sys.prefix)


def _ensure_toolchain_shims(state: RuntimeState) -> Path:
    """
    创建工具包装脚本,把命令转发给当前正在运行的 Python
    :param state:
    :return:
    """
    shim_dir = state.workplace / ".mokioclaw" / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    python_executable = sys.executable
    if os.name == "nt":
        _write_shim(shim_dir / "python.cmd", f'@echo off\r\n"{python_executable}" %*\r\n')
        _write_shim(shim_dir / "python3.cmd", f'@echo off\r\n"{python_executable}" %*\r\n')
        pip_cmd = (
            "@echo off\r\n"
            f'"{python_executable}" -c "import pathlib,sys; import pip; '
            'p=pathlib.Path(pip.__file__).resolve(); '
            'prefix=pathlib.Path(sys.prefix).resolve(); '
            'raise SystemExit(0 if p == prefix or prefix in p.parents else 1)" >nul 2>nul\r\n'
            f'if errorlevel 1 "{python_executable}" -m ensurepip --upgrade >nul 2>nul\r\n'
            f'"{python_executable}" -m pip %*\r\n'
        )
        _write_shim(shim_dir / "pip.cmd", pip_cmd)
        _write_shim(shim_dir / "pip3.cmd", pip_cmd)
        return shim_dir
    _write_shim(shim_dir / "python", f"#!/bin/sh\nexec {shlex.quote(python_executable)} \"$@\"\n")
    _write_shim(shim_dir / "python3", f"#!/bin/sh\nexec {shlex.quote(python_executable)} \"$@\"\n")
    quoted_python = shlex.quote(python_executable)
    pip_shim = (
        "#!/bin/sh\n"
        f"{quoted_python} - <<'PY' >/dev/null 2>&1\n"
        "import pathlib\n"
        "import sys\n"
        "import pip\n"
        "pip_path = pathlib.Path(pip.__file__).resolve()\n"
        "prefix = pathlib.Path(sys.prefix).resolve()\n"
        "raise SystemExit(0 if pip_path == prefix or prefix in pip_path.parents else 1)\n"
        "PY\n"
        "if [ $? -ne 0 ]; then\n"
        f"  {quoted_python} -m ensurepip --upgrade >/dev/null 2>&1 || exit $?\n"
        "fi\n"
        f"exec {quoted_python} -m pip \"$@\"\n"
    )
    _write_shim(shim_dir / "pip", pip_shim)
    _write_shim(shim_dir / "pip3", pip_shim)
    return shim_dir


def _shim_encoding() -> str:
    """
    cmd.exe 用系统代码页(中文 Windows 为 cp936)解析 .cmd/.bat 文件,
    若用 utf-8 写入, 含非 ASCII 的路径(如 ...\\Agent开发\\...)会被读成乱码,
    命令随即报 "The system cannot find the path specified"。
    """
    return "mbcs" if os.name == "nt" else "utf-8"


def _write_shim(path: Path, content: str) -> None:
    encoding = _shim_encoding()
    try:
        existing = path.read_text(encoding=encoding, errors="replace")
    except (LookupError, OSError):
        existing = None
    if existing != content:
        try:
            path.write_text(content, encoding=encoding)
        except UnicodeEncodeError:
            path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)


def _parse_env_file(path, base_env: dict[str, str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        parsed[key] = _expand_env_value(_unquote_env_value(value.strip()), {**base_env, **parsed})
    return parsed


def _unquote_env_value(value: str) -> str:
    """
    去除值两边成对的引号。
    :param value:
    :return:
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _expand_env_value(value: str, env: dict[str, str]) -> str:
    """
    展开环境变量引用。
    :param value:
    :param env:
    :return:
    """
    def replace_var(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("plain") or ""
        return env.get(name, "")

    return re.sub(r"\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|\$(?P<plain>[A-Za-z_][A-Za-z0-9_]*)", replace_var, value)


def _format_captured_output(state: RuntimeState, stdout: str, stderr: str, max_output_chars: int) -> dict[str, Any]:
    """
    限制命令输出大小，并把超长输出保存到 workplace 文件中
    :param state:
    :param stdout:
    :param stderr:
    :param max_output_chars:
    :return:
    """
    output: dict[str, Any] = {}
    output_dir = state.workplace / ".mokioclaw" / "bash-outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    if len(stdout) > max_output_chars:
        stdout_path = output_dir / f"stdout-{time.time_ns()}.log"
        stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
        output["stdout_path"] = str(stdout_path.relative_to(state.workplace))
        output["stdout_truncated"] = True
    if len(stderr) > max_output_chars:
        stderr_path = output_dir / f"stderr-{time.time_ns()}.log"
        stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
        output["stderr_path"] = str(stderr_path.relative_to(state.workplace))
        output["stderr_truncated"] = True
    output["stdout"] = stdout[:max_output_chars]
    output["stderr"] = stderr[:max_output_chars]
    return output


def _run_background(
    state: RuntimeState,
    command: str,
    env: dict[str, str],
    approval: dict[str, Any] | None,
) -> dict[str, Any]:
    output_dir = state.workplace / ".mokioclaw" / "background"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()
    stdout_path = output_dir / f"job-{stamp}.out"
    stderr_path = output_dir / f"job-{stamp}.err"
    stdout_handle = stdout_path.open("wb")
    stderr_handle = stderr_path.open("wb")
    try:
        process = subprocess.Popen(
            command,
            cwd=state.workplace,
            shell=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=(os.name != "nt"),
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()
    return {
        "ok": True,
        "timed_out": False,
        "command": command,
        "background": True,
        "pid": process.pid,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "stdout_path": str(stdout_path.relative_to(state.workplace)),
        "stderr_path": str(stderr_path.relative_to(state.workplace)),
        "duration_ms": 0,
        **(approval or {}),
    }