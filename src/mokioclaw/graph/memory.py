from datetime import datetime
import json
from typing import Any

from mokioclaw.core.state import RuntimeState
from mokioclaw.graph._utils import _short_text, _trim_handoffs
from mokioclaw.tools.file_tools import read_text_lossy
from mokioclaw.tools.notepad_tool import read_notepad, NOTEPAD_FILE

HISTORY_SUMMARY_FILE = "HISTORY_SUMMARY.md"

RULES_LAYER = {
    "scope": "workspace",
    "storage": "internal",
    "rules": [
        "Work inside the current workspace only.",
        "Use paths relative to the workspace; do not prefix paths with workspace/.",
        "Keep durable task context outside the raw messages transcript when possible.",
        "Treat TODO.md as working plan state, NOTEPAD.md as durable notes, and HISTORY_SUMMARY.md as compressed history.",
        "Do not expose memory write tools to agents; layered memory is assembled by the runtime.",
    ],
}

MAX_TEXT_CHARS = {
    "research_notes": 1600,
    "agent_handoff_instruction": 500,
    "agent_handoff_result": 700,
    "code_agent_summary": 1000,
    "verifier_summary": 1000,
    "last_error": 1400,
    "context_summary": 1600,
    "notepad": 1800,
    "history_summary": 2200,
}


def read_history_summary(state: RuntimeState):
    path = state.assert_workplace_path(state.workplace / HISTORY_SUMMARY_FILE)
    if not path.exists():
        return {"ok": True, "path": HISTORY_SUMMARY_FILE, "content": "", "exists": False}
    content = read_text_lossy(path)
    state.record_read(path, complete=True)
    return {"ok": True, "path": HISTORY_SUMMARY_FILE, "content": content, "exists": True}


def build_layered_memory(state: dict[str, Any],*,node:str="graph") -> dict[str, Any]:
    runtime = state["runtime"]
    notepad = read_notepad(runtime) # 读取notepad文件内容
    history = read_history_summary(runtime) # 读取HISTORY_SUMMARY文件内容
    sources = [
        {
            "title": source.get("title", ""),
            "url": source.get("url", ""),
        }
        for source in state.get("sources", [])
    ]
    working_memory = {
        "node": node,
        "task": state.get("task", ""),
        "plan_summary": state.get("plan_summary", ""),
        "todos": state.get("todos", []),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "verification_commands": state.get("verification_commands", []),
        "research_notes": _short_text(state.get("research_notes", ""), MAX_TEXT_CHARS["research_notes"]),
        "sources": sources,
        "agent_handoffs": _trim_handoffs(state.get("agent_handoffs", [])),
        "code_agent_summary": _short_text(state.get("code_agent_summary", ""), MAX_TEXT_CHARS["code_agent_summary"]),
        "verifier_summary": _short_text(state.get("verifier_summary", ""), MAX_TEXT_CHARS["verifier_summary"]),
        "verification_checks": state.get("verification_checks", []),
        "last_error": _short_text(state.get("last_error", ""), MAX_TEXT_CHARS["last_error"]),
        "attempts": state.get("attempts", 0),
        "max_attempts": state.get("max_attempts", 3),
        "context_next_node": state.get("context_next_node", ""),
    }
    history_summary = state.get("history_summary") or history.get("content", "")
    history_summary_store = {
        "history_path": HISTORY_SUMMARY_FILE,
        "history_exists": history.get("exists", False),
        "history_summary": _short_text(history_summary, MAX_TEXT_CHARS["history_summary"]),
        "notepad_path": NOTEPAD_FILE,
        "notepad_exists": notepad.get("exists", False),
        "notepad": _short_text(notepad.get("content", ""), MAX_TEXT_CHARS["notepad"]),
        "context_summary": _short_text(state.get("context_summary", ""), MAX_TEXT_CHARS["context_summary"]),
        "compression_events": state.get("compression_events", [])[-3:],
    }
    return {
        "rules": dict(RULES_LAYER),
        "working_memory": working_memory,
        "history_summary_store": history_summary_store,
    }

def format_layered_memory_for_prompt(memory: dict[str, Any]) -> str:
    """
    dict 转 json
    :param memory:
    :return:
    """
    return json.dumps(memory, ensure_ascii=False, indent=2, default=str)


def memory_event(memory: dict[str, Any], *, node: str) -> dict[str, Any]:
    """
    dict 类型 snapshot
    :param memory:
    :param node:
    :return:
    """
    working = memory.get("working_memory", {})
    history = memory.get("history_summary_store", {})
    return {
        "type": "memory_snapshot",
        "node": node,
        "rules_count": len(memory.get("rules", {}).get("rules", [])),
        "todo_count": len(working.get("todos", [])),
        "source_count": len(working.get("sources", [])),
        "handoff_count": len(working.get("agent_handoffs", [])),
        "notepad_exists": bool(history.get("notepad_exists")),
        "history_exists": bool(history.get("history_exists")),
        "history_path": history.get("history_path", HISTORY_SUMMARY_FILE),
        "layers": {
            "rules": _event_layer_summary(memory.get("rules", {})),
            "working_memory": _event_layer_summary(working),
            "history_summary_store": _event_layer_summary(history),
        },
    }


def _event_layer_summary(layer: dict[str, Any]) -> str:
    """
    dict 转 json
    :param layer:
    :return: json
    """
    if not layer:
        return "(empty)"
    text = json.dumps(layer, ensure_ascii=False, default=str)
    return _short_text(text, 420)


def persist_history_summary(state: RuntimeState, summary: str) -> dict[str, Any]:
    path = state.assert_workplace_path(state.workplace / HISTORY_SUMMARY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"# MokioClaw History Summary\n\n_Updated: {timestamp}_\n\n{summary.strip()}\n"
    path.write_text(content, encoding="utf-8")
    state.record_read(path, complete=True)
    return {"ok": True, "path": HISTORY_SUMMARY_FILE, "lines": len(content.splitlines())}

