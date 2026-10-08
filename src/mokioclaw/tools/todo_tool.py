import json
from typing import Any

from mokioclaw.core.state import RuntimeState

VALID_TODO_STATUSES = {"pending", "in_progress", "completed", "blocked"}
TODO_FILE = "TODO.md"

def _normalize_items(items):
    """
    把不同格式的输入统一转换成干净的字符串列表 list[str]。
    :param items:
    :return:
    """
    if isinstance(items, str):
        stripped = items.strip()
        if not stripped:
            return []
        try:
            decode = json.loads(stripped)
        except json.decoder.JSONDecodeError:
            return [line.strip("-") for line in stripped.splitlines() if line.strip()]
        return _normalize_items(decode)
    if isinstance(items, dict):
        value = items.get("content") or items.get("description") or items.get("title") or items.get("text") or items.get("command")
        if value:
            return [str(value).strip()]
        normalized: list[str] = []
        for key, item in items.items():
            child_items = _normalize_items(item)
            normalized.extend(child_items or [str(key).strip()])
        return [item for item in normalized if item]
    if isinstance(items, list):
        normalized: list[str] = []
        for item in items:
            normalized.extend(_normalize_items(item))
        return [item for item in normalized if item]
    return [str(items).strip()] if items is not None and str(items).strip() else []

def write_todos(todos:list[str], acceptance_criteria:list[str],verification_command:list[str]):
    cleaned_todos = _normalize_items(todos)
    cleaned_verification_command = _normalize_items(verification_command)
    cleared_acceptance_criteria = _normalize_items(acceptance_criteria)
    return {
        'ok':  bool(cleaned_todos and cleaned_verification_command and cleared_acceptance_criteria),
        'todos': cleaned_todos,
        'verification_commands': cleaned_verification_command,
        'acceptance_criteria': cleared_acceptance_criteria
    }

def update_todo(todos:list[dict[str,str]], todo_id, status, note:str=""):
    """
    更新指定待办事项的状态和备注
    :param todos:
    :param todo_id:
    :param status:
    :param note:
    :return:
    """
    if status not in VALID_TODO_STATUSES:
        return {
            'ok': False,
            "error": f"status must be one of: {', '.join(sorted(VALID_TODO_STATUSES))}",
            "todos": todos,
        }

    updated: list[dict[str,str]] = []
    found= False
    for todo in todos:
        item = dict(todo)
        if item.get("id") == todo_id:
            item['status'] = status
            item['note'] = note
            found = True
        updated.append(item)

    if not found:
        return {
            'ok': False,
            "error": f"unknown todo_id: {todo_id}",
            "todos": todos
        }
    return {
        "ok": True,
        "todo_id": todo_id,
        "status": status,
        "note": note,
        "todos": updated
    }


def render_todo_markdown(
    todos: list[dict[str, Any]],
    acceptance_criteria: list[str],
    verification_commands: list[str],
    plan_summary: str = "",
) -> str:
    lines = ["# MokioClaw Todo", ""]
    if plan_summary:
        lines.extend(["## Plan", "", plan_summary, ""])
    lines.extend(["## Todos", ""])
    if todos:
        for todo in todos:
            status = str(todo.get("status", "pending"))
            box = {"pending": " ", "in_progress": "-", "completed": "x", "blocked": "!"}.get(status, " ")
            note = str(todo.get("note", ""))
            note_text = f" — {note}" if note else ""
            lines.append(f"- [{box}] **{todo.get('id', '')}** `{status}` {todo.get('content', '')}{note_text}")
    else:
        lines.append("- [ ] No todos yet.")
    if acceptance_criteria:
        lines.extend(["", "## Acceptance Criteria", ""])
        lines.extend(f"- {item}" for item in acceptance_criteria)
    if verification_commands:
        lines.extend(["", "## Verification Commands", ""])
        lines.extend(f"- `{command}`" for command in verification_commands)
    lines.append("")
    return "\n".join(lines)


def persist_todos(
        state:RuntimeState,
        todos:list[dict[str,str]],
        verification_command:list[str],
        acceptance_criteria:list[str],
        plan_summary:str=""
):
    path = state.assert_workplace_path(state.workplace / TODO_FILE)
    content = render_todo_markdown(todos, acceptance_criteria or [], verification_command or [], plan_summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    state.record_read(path,complete=True)
    return {'ok': True, 'path': TODO_FILE, 'lines': len(content.splitlines()), "todos": todos}