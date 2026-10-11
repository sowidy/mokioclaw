import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from mokioclaw.core.checkpoint import workplace_manifest


SESSION_ROOT = Path(".mokioclaw") / "session"
SESSION_FILE = "session.json"
SESSION_SUMMARY_FILE = "SESSION_SUMMARY.md"
MAX_RECENT_TURNS = 18
MAX_TURN_CONTENT = 1800
MAX_SESSION_SUMMARY = 5000
MAX_SESSION_CONTEXT = 7000


def session_dir(workplace: Path) -> Path:
    return workplace / SESSION_ROOT


def session_file(workplace: Path) -> Path:
    return session_dir(workplace) / SESSION_FILE


def session_summary_file(workplace: Path) -> Path:
    return workplace / SESSION_SUMMARY_FILE


def load_or_create_session(workplace: Path) -> dict[str, Any]:
    """
    创建session文件夹并保存session文件
    :param workplace:
    :return:
    """
    workplace.mkdir(parents=True, exist_ok=True)
    path = session_file(workplace)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    else:
        raw = {}
    session = _normalize_session(raw, workplace)
    save_session(workplace, session)
    return session


def append_user_turn(session: dict[str, Any], content: str) -> int:
    """
    往session中添加内容信息，比如本轮任务等
    :param session:
    :param content: 本轮任务
    :return:
    """
    turn = int(session.get("turn_index", 0)) + 1
    session["turn_index"] = turn
    session["last_task"] = content
    _append_turn(
        session,
        {
            "turn": turn,
            "role": "user",
            "route": "",
            "content": trim_text(content, MAX_TURN_CONTENT),
            "timestamp": utc_now(),
        },
    )
    return turn


def append_assistant_turn(
    session: dict[str, Any],
    *,
    turn: int,
    route: str,
    content: str,
    summary: str = "",
) -> None:
    """
    大模型对话一次记录一次
    :param session:
    :param turn:
    :param route:
    :param content:
    :param summary:
    :return:
    """
    session["last_route"] = route
    session["last_final_answer"] = trim_text(content, MAX_TURN_CONTENT)
    _append_turn(
        session,
        {
            "turn": turn,
            "role": "assistant",
            "route": route,
            "content": trim_text(content, MAX_TURN_CONTENT),
            "summary": trim_text(summary or content, 700),
            "timestamp": utc_now(),
        },
    )


def save_session(workplace: Path, session: dict[str, Any]) -> dict[str, Any]:
    """
    保存到本地文件
    :param workplace:
    :param session:
    :return:
    """
    workplace.mkdir(parents=True, exist_ok=True)
    session = _normalize_session(session, workplace)
    _compact_session(session)
    session["updated_at"] = utc_now()
    path = session_file(workplace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    session_summary_file(workplace).write_text(build_session_summary_markdown(workplace, session), encoding="utf-8")
    return session


def build_session_context(workplace: Path, session: dict[str, Any] | None = None) -> str:
    """
    从本地保存位置加载session信息，包括session_id和recent_turns
    :param workplace:
    :param session:
    :return:
    """
    session = _normalize_session(session or load_or_create_session(workplace), workplace)
    manifest = workplace_manifest(workplace, limit=40)
    files = [
        item.get("path", "")
        for item in manifest
        if item.get("type") == "file" and not str(item.get("path", "")).startswith(".mokioclaw/session/")
    ][:30]
    recent_turns = [
        {
            "turn": turn.get("turn"),
            "role": turn.get("role", ""),
            "route": turn.get("route", ""),
            "content": trim_text(str(turn.get("content", "")), 600),
            "summary": trim_text(str(turn.get("summary", "")), 300),
        }
        for turn in session.get("recent_turns", [])[-10:]
    ]
    payload = {
        "session_id": session.get("session_id", ""),
        "turn_index": session.get("turn_index", 0),
        "workplace": str(workplace),
        "summary": trim_text(str(session.get("summary", "")), 1800),
        "last_route": session.get("last_route", ""),
        "last_task": trim_text(str(session.get("last_task", "")), 600),
        "last_final_answer": trim_text(str(session.get("last_final_answer", "")), 900),
        "recent_turns": recent_turns,
        "recent_files": files,
    }
    return trim_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), MAX_SESSION_CONTEXT)


def session_started_event(workplace: Path, session: dict[str, Any], *, resumed: bool = False) -> dict[str, Any]:
    """
    构造一个“会话已启动”事件对象，本身不执行模型调用，也不保存文件
    :param workplace:
    :param session:
    :param resumed:
    :return:
    """
    return {
        "type": "session_started",
        "session_id": session.get("session_id", ""),
        "workplace": str(workplace),
        "turn_index": session.get("turn_index", 0),
        "resumed": resumed,
        "session_file": str(session_file(workplace)),
        "summary_file": str(session_summary_file(workplace)),
    }


def session_turn_started_event(workplace: Path, session: dict[str, Any], *, turn: int, task: str) -> dict[str, Any]:
    """
    构造“当前用户回合开始”事件
    :param workplace:
    :param session:
    :param turn:
    :param task:
    :return:
    """
    return {
        "type": "session_turn_started",
        "session_id": session.get("session_id", ""),
        "workplace": str(workplace),
        "turn": turn,
        "task": trim_text(task, 900),
    }


def session_turn_saved_event(workplace: Path, session: dict[str, Any], *, turn: int, route: str) -> dict[str, Any]:
    return {
        "type": "session_turn_saved",
        "session_id": session.get("session_id", ""),
        "workplace": str(workplace),
        "turn": turn,
        "route": route,
        "turn_count": len(session.get("recent_turns", [])),
        "summary_file": str(session_summary_file(workplace)),
    }


def build_session_summary_markdown(workplace: Path, session: dict[str, Any]) -> str:
    lines = [
        "# MokioClaw Session Summary",
        "",
        f"- session_id: {session.get('session_id', '')}",
        f"- workplace: {workplace}",
        f"- turns: {session.get('turn_index', 0)}",
        f"- updated_at: {session.get('updated_at', '')}",
        f"- last_route: {session.get('last_route', '') or '(none)'}",
        "",
        "## Summary",
        "",
        str(session.get("summary", "") or "(no compressed summary yet)"),
        "",
        "## Recent Turns",
        "",
    ]
    for turn in session.get("recent_turns", [])[-MAX_RECENT_TURNS:]:
        role = turn.get("role", "")
        route = f" / {turn.get('route')}" if turn.get("route") else ""
        lines.append(f"- turn {turn.get('turn')}: {role}{route}: {trim_text(str(turn.get('content', '')), 260)}")
    return "\n".join(lines).rstrip() + "\n"


def trim_text(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_session(raw: dict[str, Any], workplace: Path) -> dict[str, Any]:
    now = utc_now()
    session = dict(raw) if isinstance(raw, dict) else {}
    session.setdefault("version", 1)
    session.setdefault("session_id", f"session-{uuid4().hex[:8]}")
    session.setdefault("workplace", str(workplace))
    session.setdefault("created_at", now)
    session.setdefault("updated_at", now)
    session.setdefault("turn_index", 0)
    session.setdefault("summary", "")
    session.setdefault("recent_turns", [])
    session.setdefault("last_route", "")
    session.setdefault("last_task", "")
    session.setdefault("last_final_answer", "")
    if not isinstance(session.get("recent_turns"), list):
        session["recent_turns"] = []
    session["workplace"] = str(workplace)
    session["turn_index"] = int(session.get("turn_index") or 0)
    session["summary"] = trim_text(str(session.get("summary", "")), MAX_SESSION_SUMMARY)
    return session


def _append_turn(session: dict[str, Any], turn: dict[str, Any]) -> None:
    """
    session[recent_turns] 中加入本 turn 信息
    :param session:
    :param turn:
    :return:
    """
    turns = list(session.get("recent_turns", []))
    turns.append(turn)
    session["recent_turns"] = turns


def _compact_session(session: dict[str, Any]) -> None:
    """
    压缩session信息为json
    :param session:
    :return:
    """
    turns = list(session.get("recent_turns", []))
    if len(turns) <= MAX_RECENT_TURNS:
        return
    older = turns[: -MAX_RECENT_TURNS] # 前x个元素
    kept = turns[-MAX_RECENT_TURNS:]
    additions = []
    for turn in older:
        role = turn.get("role", "")
        route = f"/{turn.get('route')}" if turn.get("route") else ""
        summary = turn.get("summary") or turn.get("content", "")
        additions.append(f"turn {turn.get('turn')}: {role}{route}: {trim_text(str(summary), 260)}")
    existing = str(session.get("summary", ""))
    session["summary"] = trim_text("\n".join(part for part in [existing, *additions] if part), MAX_SESSION_SUMMARY)
    session["recent_turns"] = kept
