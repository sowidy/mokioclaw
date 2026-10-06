import os
import platform
import re
import shlex #
import subprocess
import time

from mokioclaw.core.state import RuntimeState

DEFAULT_TIMEOUT_SECONDS = 10
MAX_OUTPUT_CHARS = 6000

DANGEROUS_PATTERNS = {
    r"\brm\s+-rf\b",
    r"\bRemove-Item\b.*\b-Recurse\b.*\b-Force\b",
    r"\bdel\s+/[sq]\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r">\s*(?:[A-Za-z]:\\|/)",
}

def bash_tool_description() -> str:
    system = platform.system().lower()
    common = (
        "Run a safe development shell command inside the workspace with timeout and output capture. "
        "The command already runs with cwd set to the workspace, so use relative paths and do not run cd /workspace, "
        "cd workspace, or long-lived interactive commands. Prefer cross-platform Python one-liners for file checks."
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
    try:
        return int(timeout_seconds)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def _normalize_command(command: str) -> str:
    if os.name == "nt":
        normalized = re.sub(r"^\s*python3(\.exe)?\b", "python", command, count=1,flags=re.IGNORECASE)
        normalized = re.sub(r"\bls\s+-la\b", "dir", normalized)
        normalized = re.sub(r"\bls\b", "dir", normalized)
        normalized = re.sub(r"\bcat\s+([^\s|&<>]+)", r"type \1", normalized)
        return normalized
    return command


def _handle_tail_command(state: RuntimeState, command: str):
    match = re.fullmatch(r"\s*tail(?:\s+-n)?\s+(\d+)\s+(.+?)\s*", command)
    if not match:
        match = re.fullmatch(r"\s*tail\s+-(\d+)\s+(.+?)\s*", command)
    if not match:
        return None
    count = int(match.group(1))
    raw_path = shlex.split(match.group(2), posix=False)[0]
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

def run_bash(state: RuntimeState, command: str,timeout_seconds:int | str | float =DEFAULT_TIMEOUT_SECONDS):
    if not command.strip():
        return {
            'ok': False,
            'error': "command is empty",
        }
    timeout = _coerce_timeout(timeout_seconds)
    if timeout <= 0 or timeout > 60:
        return {
            'ok': False,
            'error': f"timeout {timeout} seconds exceeded",
        }
    normalized_command = _normalize_command(command)
    handled = _handle_tail_command(state, normalized_command)
    if handled is not None:
        return handled
    block = _looks_dangerous(normalized_command)
    if block:
        return {
            'ok': True,
            'error': f"blocked potentially dangerous command pattern: {block}",
        }
    started = time.perf_counter()
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    try:
        completed = subprocess.run(
            normalized_command,
            cwd=state.workspace,
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
            "stdout": (exc.stdout or "")[:MAX_OUTPUT_CHARS],
            "stderr": (exc.stderr or "")[:MAX_OUTPUT_CHARS],
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }

    stdout = completed.stdout[:MAX_OUTPUT_CHARS]
    stderr = completed.stderr[:MAX_OUTPUT_CHARS]
    return {
        'ok': completed.returncode == 0,
        'timed_out': False,
        'command': normalized_command,
        'exit_code': completed.returncode,
        'stdout': stdout,
        'stderr': stderr,
        'duration_ms': round((time.perf_counter() - started) * 1000),
    }