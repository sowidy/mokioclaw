import json

VALID_TODO_STATUSES = {"pending", "in_progress", "completed", "blocked"}

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
        value = items.get("content") or items.get("title") or items.get("command")
        return [str(value).strip()] if value else []
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