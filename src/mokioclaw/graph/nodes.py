import json
import os
import re
from json import JSONDecodeError
from typing import Any

from dotenv import load_dotenv
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from mokioclaw.agents.code_agent import run_code_agent
from mokioclaw.agents.search_agent import run_search_agent, _dedupe_sources, _last_ai_content
from mokioclaw.prompts.stage3 import VERIFIER_PROMPT
from mokioclaw.prompts.stage4 import CONTEXT_COMPRESSION_PROMPT
from mokioclaw.tools.registry import build_read_only_tools
from mokioclaw.tools.todo_tool import write_todos
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage, RemoveMessage
from mokioclaw.graph.state import MokioGraphState, TodoItem, VerificationCheck
from mokioclaw.prompts.stage2 import PLANNER_PROMPT
from mokioclaw.providers.model_provider import create_model

DEFAULT_CONTEXT_TOKEN_LIMIT = 400000

YOIMIYA_TODOS = [
    "Research Yoimiya (宵宫) from Genshin Impact and collect reliable source links.",
    "Create yoimiya_profile.html with a polished character introduction.",
    "Include at least two source links in the HTML.",
    "Run non-interactive checks for the generated HTML file.",
]

YOIMIYA_CRITERIA = [
    "yoimiya_profile.html exists in the workspace.",
    "The page mentions 宵宫 and 原神.",
    "The page introduces identity, traits, abilities, and story role.",
    "The page includes at least two source links.",
]

YOIMIYA_COMMANDS = [
    "python -c \"from pathlib import Path; p=Path('yoimiya_profile.html'); s=p.read_text(encoding='utf-8'); assert '宵宫' in s and '原神' in s; assert s.lower().count('http') >= 2; print('yoimiya html ok')\"",
]

DEFAULT_TODOS = [
    "Clarify the deliverable and acceptance criteria.",
    "Delegate specialist work needed for the task.",
    "Verify the generated result.",
]

def _extract_json(text:str):
    """
    从文本中提取一个 JSON 对象，并转换成 Python 字典。
    :param text:
    :return:
    """
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL) #
    raw = fence.group(1) if fence else text
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or start > end:
        return None
    try:
        parse = json.loads(raw[start:end + 1]) # loads 是从字符串解析 JSON
    except json.decoder.JSONDecodeError:
        return None
    return parse if isinstance(parse, dict) else None


def _is_yoimiya_task(task: str) -> bool:
    lowered = task.lower()
    return "宵宫" in task or "yoimiya" in lowered or "genshin" in lowered or "原神" in task



def _default_plan(task: str) -> dict[str, Any]:
    if _is_yoimiya_task(task):
        return {
            "plan_summary": "Research Yoimiya (宵宫) from Genshin Impact and build a sourced HTML character profile.",
            "todos": YOIMIYA_TODOS,
            "acceptance_criteria": YOIMIYA_CRITERIA,
            "verification_commands": YOIMIYA_COMMANDS,
        }
    return {
        "plan_summary": "Coordinate specialist agents to complete and verify the requested deliverable.",
        "todos": DEFAULT_TODOS,
        "acceptance_criteria": ["The requested deliverable exists.", "The verifier model confirms completion."],
        "verification_commands": [],
    }

def _todo_items(todos:list[str],*,existing:list[dict[str,Any]] | None = None): # 函数参数中的 * 表示：后面的参数只能使用关键字参数传入，不能使用位置参数传入。
    existing_by_content = {todo.get('content',""):todo for todo in existing or []}
    items : list[TodoItem] = []
    for idx, todo in enumerate(todos, start=1):
        previous = existing_by_content.get(todo,{})
        items.append({
            'id':str(previous.get('id',f"todo-{idx}")),
            'content':todo,
            'status':str(previous.get('status',"pending")),
            'note':str(previous.get('note',"")),
        })
    return items

def _verification_commands_for_task(task: str, parsed: dict[str, Any]) -> list[str]:
    if _is_yoimiya_task(task):
        return YOIMIYA_COMMANDS
    return [str(item) for item in parsed.get("verification_commands") or []]

def _apply_plan(state: MokioGraphState, plan:dict[str, Any]):
    state['plan_summary'] = str(plan.get("plan_summary",""))
    state['todos'] = _todo_items([str(todo) for todo in plan.get("todos", [])],existing=state.get("todos", []))

def _todo_write_tool(
    state: MokioGraphState,
    writer,
    todos: Any,
    acceptance_criteria: Any,
    verification_commands: Any,
    plan_summary: str = "",
) -> dict[str, Any]:
    result = write_todos(todos, acceptance_criteria, verification_commands)
    if result.get("ok"):
        state["plan_summary"] = plan_summary or state.get("plan_summary") or "MultiAgent plan"
        state["todos"] = _todo_items(result["todos"], existing=state.get("todos", []))
        state["acceptance_criteria"] = result["acceptance_criteria"]
        state["verification_commands"] = result["verification_commands"]
        writer(
            {
                "type": "plan_snapshot",
                "node": "planner",
                "plan_summary": state.get("plan_summary", ""),
                "todos": state.get("todos", []),
                "verification_commands": state.get("verification_commands", []),
                "acceptance_criteria": state.get("acceptance_criteria", []),
            }
        )
    return {
        **result,
        "plan_summary": state.get("plan_summary", ""),
        "todo_items": state.get("todos", []),
    }


def _join_notes(existing: str, new: str) -> str:
    if not existing:
        return new
    if not new:
        return existing
    return existing + "\n\n" + new

def _call_search_agent_tool(state:MokioGraphState, writer, instruction:str):
    writer({"type": "handoff", "from": "planner", "to": "searchAgent", "instruction": instruction})
    result = run_search_agent(state, instruction, writer=writer)
    existing_sources = list(state.get('sources', []))
    state['research_notes'] = _join_notes(state.get('research_notes', ""), result.get('summary', ""))
    state['sources'] = _dedupe_sources(existing_sources + list(result.get('sources', [])))
    handoff = {
        'from_agent': 'planner',
        'to_agent': 'searchAgent',
        'instruction': instruction,
        'result': result.get('summary',""),
    }
    state['agent_handoffs'] = list(state.get('agent_handoffs',[])) + [handoff]
    writer({"type": "handoff_result", "from": "searchAgent", "to": "planner", "result": result.get("summary", "")})
    return {
        "ok": True,
        "summary": result.get("summary", ""),
        "sources": state.get("sources", []),
        "queries": result.get("queries", []),
    }

def _call_code_agent_tool(state:MokioGraphState, writer, instruction:str):
    writer({"type": "handoff", "from": "planner", "to": "codeAgent", "instruction": instruction})
    result = run_code_agent(state, instruction, writer=writer)
    state['todos'] = result.get("todos", state.get("todos", []))
    state["code_agent_summary"] = result.get("summary", "")
    state["last_actor_summary"] = result.get("summary", "")
    handoff = {
        "from_agent": "planner",
        "to_agent": "codeAgent",
        "instruction": instruction,
        "result": result.get("summary", ""),
    }

    state["agent_handoffs"] = list(state.get("agent_handoffs", [])) + [handoff]
    writer({"type": "handoff_result", "from": "codeAgent", "to": "planner", "result": result.get("summary", "")})
    return {
        "ok": True,
        "summary": result.get("summary", ""),
        "todos": state.get("todos", [])
    }



def _build_planner_tools(state: MokioGraphState, writer):
    return [
        StructuredTool.from_function(
            name="TodoWriteTool",
            func=lambda todos, acceptance_criteria, verification_commands, plan_summary="": _todo_write_tool(
                state, writer, todos, acceptance_criteria, verification_commands, plan_summary
            ),
            description=(
                "Publish or revise plan state. Args: todos, acceptance_criteria, "
                "verification_commands, optional plan_summary."
            ),
        ),
        StructuredTool.from_function(
            name="CallSearchAgentTool",
            func=lambda instruction: _call_search_agent_tool(state, writer, instruction),
            description="Delegate research work to searchAgent. Args: instruction.",
        ),
        StructuredTool.from_function(
            name="CallCodeAgentTool",
            func=lambda instruction: _call_code_agent_tool(state, writer, instruction),
            description="Delegate implementation work to codeAgent. Args: instruction.",
        ),
    ]

def _todos_text(todos: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- {todo.get('id', '')} [{todo.get('status', '')}] {todo.get('content', '')} {todo.get('note', '')}".strip()
        for todo in todos
    )

def _list_text(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _planner_input(state: MokioGraphState) -> str:
    source_text = "\n".join(f"- {source.get('title', '')}: {source.get('url', '')}" for source in state.get("sources", []))
    return (
        f"Task: {state['task']}\n"
        f"Attempt: {state.get('attempts', 0) + 1}\n\n"
        f"Current plan: {state.get('plan_summary', '')}\n"
        f"Todos:\n{_todos_text(state.get('todos', []))}\n\n"
        f"Acceptance criteria:\n{_list_text(state.get('acceptance_criteria', []))}\n\n"
        f"Verification commands:\n{_list_text(state.get('verification_commands', []))}\n\n"
        f"Research notes:\n{state.get('research_notes', '')}\n\n"
        f"Sources:\n{source_text}\n\n"
        f"CodeAgent summary:\n{state.get('code_agent_summary', '')}\n\n"
        f"Previous verifier failure:\n{state.get('last_error', '')}"
        f"\n\nCompressed context summary, if any:\n{state.get('context_summary', '')}"
    )


def _execute_planner_tool(state: MokioGraphState, writer, call: dict[str, Any]) -> ToolMessage:
    name = call.get("name", "")
    args = call.get("args") or {}
    writer({"type": "tool_call", "node": "planner", "name": name, "args": args})
    tools = {tool.name: tool for tool in _build_planner_tools(state, writer)}
    tool = tools.get(name)
    if tool is None:
        result = {"ok": False, "error": f"unknown tool: {name}"}
    else:
        try:
            result = tool.invoke(args)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    tool_message = ToolMessage(
        content=json.dumps(result, ensure_ascii=False),
        name=name,
        tool_call_id=call.get("id") or f"{name}-call",
    )
    writer(_tool_result_event(tool_message, node="planner"))
    return tool_message


def planner_node(state: MokioGraphState):
    writer = _get_writer()
    working_state : MokioGraphState = {**state} # 创建 state 的一个浅拷贝
    if not working_state.get("todos"):
        _apply_plan(working_state,_default_plan(working_state['task']))
    model1 = create_model()

    planner = model1.bind_tools(_build_planner_tools(working_state, writer))
    messages: list[Any] = [
        SystemMessage(content=PLANNER_PROMPT),
        HumanMessage(content=_planner_input(working_state)),
    ]

    produced_messages: list[Any] = []

    writer(
        {
            "type": "plan_snapshot",
            "node": "planner",
            "plan_summary": working_state.get("plan_summary", ""),
            "todos": working_state.get("todos", []),
            "verification_commands": working_state.get("verification_commands", []),
            "attempts": working_state.get("attempts", 0),
        }
    )
    for _ in range(8):
        response = planner.invoke(messages)
        produced_messages.append(response)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break
        for call in tool_calls:
            tool_message = _execute_planner_tool(working_state, writer, call)
            produced_messages.append(tool_message)
            messages.append(tool_message)
    else:
        produced_messages.append(AIMessage(content="planner stopped after the maximum supervisor tool loop count."))

    metadata = dict(working_state.get("metadata", {}))
    metadata["planner_raw"] = _last_ai_content(produced_messages)
    return {
        "plan_summary": working_state.get("plan_summary", ""),
        "todos": working_state.get("todos", []),
        "acceptance_criteria": working_state.get("acceptance_criteria", []),
        "verification_commands": working_state.get("verification_commands", []),
        "research_notes": working_state.get("research_notes", ""),
        "sources": working_state.get("sources", []),
        "agent_handoffs": working_state.get("agent_handoffs", []),
        "code_agent_summary": working_state.get("code_agent_summary", ""),
        "last_actor_summary": working_state.get("code_agent_summary", ""),
        "messages": produced_messages,
        "metadata": metadata,
        "context_next_node": "verifier",
    }


def _get_writer():
    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _:None


def _tool_result_event(tool_message,*, node: str):
    try:
        parsed = json.loads(tool_message.content)
    except JSONDecodeError:
        parsed = tool_message.content
    return {
        'type': 'tool_result',
        'node': node,
        "name": tool_message.name,
        "result": parsed,
    }



def _verifier_input(state: MokioGraphState) -> str:
    source_text = "\n".join(f"- {source.get('title', '')}: {source.get('url', '')}" for source in state.get("sources", []))
    return (
        f"Task: {state['task']}\n\n"
        f"Plan: {state.get('plan_summary', '')}\n\n"
        f"Todos:\n{_todos_text(state.get('todos', []))}\n\n"
        f"Acceptance criteria:\n{_list_text(state.get('acceptance_criteria', []))}\n\n"
        f"Verification commands:\n{_list_text(state.get('verification_commands', []))}\n\n"
        f"Research notes:\n{state.get('research_notes', '')}\n\n"
        f"Sources:\n{source_text}\n\n"
        f"CodeAgent summary:\n{state.get('code_agent_summary', '')}\n\n"
        f"Compressed context summary:\n{state.get('context_summary', '')}\n\n"
        "Inspect the workspace with tools and return only verifier JSON."
    )


def _execute_read_only_tool(state: MokioGraphState, call: dict[str, Any]) -> ToolMessage:
    name = call.get("name", "")
    args = call.get("args") or {}
    tools = {tool.name: tool for tool in build_read_only_tools(state["runtime"])}
    tool = tools.get(name)
    if tool is None and not name.endswith("Tool"):
        tool = tools.get(name + "Tool")
    if tool is None:
        result = {"ok": False, "error": f"unknown tool: {name}"}
    else:
        try:
            result = tool.invoke(args)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return ToolMessage(
        content=json.dumps(result, ensure_ascii=False),
        name=name,
        tool_call_id=call.get("id") or f"{name}-call",
    )


def _normalize_checks(raw: Any) -> list[VerificationCheck]:
    if not isinstance(raw, list):
        return []
    checks: list[VerificationCheck] = []
    for item in raw:
        if isinstance(item, dict):
            checks.append(
                {
                    "name": str(item.get("name") or "check"),
                    "passed": bool(item.get("passed")),
                    "detail": str(item.get("detail") or ""),
                }
            )
    return checks


def _format_verifier_error(reason: str, recommended: str, tool_events: list[dict[str, Any]]) -> str:
    event_text = json.dumps(tool_events[-3:], ensure_ascii=False, default=str)[:1600]
    return (
        f"Verifier failed: {reason}\n"
        f"Recommended next instruction: {recommended}\n"
        f"Recent verifier tool events:\n{event_text}"
    )


def _tool_events_to_verification_results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for event in events:
        result = event.get("result", {})
        if not isinstance(result, dict):
            continue
        results.append(
            {
                "command": result.get("command") or event.get("name", ""),
                "ok": bool(result.get("ok")),
                "exit_code": result.get("exit_code"),
                "stdout": str(result.get("stdout", "")),
                "stderr": str(result.get("stderr") or result.get("error", "")),
            }
        )
    return results


def verifier_node(state: MokioGraphState):
    writer = _get_writer()
    writer(
        {
            "type": "plan_snapshot",
            "node": "verifier",
            "plan_summary": state.get("plan_summary", ""),
            "todos": state.get("todos", []),
            "verification_commands": state.get("verification_commands", []),
        }
    )
    model = create_model()
    verifier = model.bind_tools(build_read_only_tools(state["runtime"]))
    messages: list[Any] = [
        SystemMessage(content=VERIFIER_PROMPT),
        HumanMessage(content=_verifier_input(state)),
    ]
    produced_messages: list[Any] = []
    tool_events: list[dict[str, Any]] = []

    for _ in range(8):
        response = verifier.invoke(messages)
        produced_messages.append(response)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break
        for call in tool_calls:
            writer({"type": "tool_call", "node": "verifier", "name": call.get("name"), "args": call.get("args", {})})
            tool_message = _execute_read_only_tool(state, call)
            event = _tool_result_event(tool_message, node="verifier")
            tool_events.append(event)
            writer(event)
            produced_messages.append(tool_message)
            messages.append(tool_message)

    else:
        produced_messages.append(
            AIMessage(
                content=json.dumps(
                    {
                        "passed": False,
                        "reason": "Verifier stopped after the maximum tool loop count.",
                        "checks": [],
                        "recommended_next_instruction": "Inspect the workspace and complete the unfinished task.",
                    },
                    ensure_ascii=False,
                )
            )
        )

    parsed = _extract_json(_last_ai_content(produced_messages)) or {
        "passed": False,
        "reason": "Verifier did not return valid JSON.",
        "checks": [
            {
                "name": "verifier_json",
                "passed": False,
                "detail": _last_ai_content(produced_messages)[:800],
            }
        ],
        "recommended_next_instruction": "Return valid verifier JSON after inspecting the result.",
    }
    checks = _normalize_checks(parsed.get("checks"))
    passed = bool(parsed.get("passed"))
    reason = str(parsed.get("reason") or "")
    recommended = str(parsed.get("recommended_next_instruction") or "")
    attempts = state.get("attempts", 0) + 1
    todos = [dict(todo) for todo in state.get("todos", [])]
    if passed:
        todos = [
            {
                **todo,
                "status": "completed" if todo.get("status") != "blocked" else todo.get("status", "blocked"),
                "note": todo.get("note") or "verified",
            }
            for todo in todos
        ]
        writer(
            {
                "type": "todo_update",
                "node": "verifier",
                "plan_summary": state.get("plan_summary", ""),
                "todos": todos,
                "verification_commands": state.get("verification_commands", []),
            }
        )
    last_error = "" if passed else _format_verifier_error(reason, recommended, tool_events)

    return {
        "messages": produced_messages,
        "verification_results": _tool_events_to_verification_results(tool_events),
        "verification_checks": checks,
        "verifier_summary": reason,
        "passed": passed,
        "attempts": attempts,
        "last_error": last_error,
        "todos": todos,
        "context_next_node": verifier_route({**state, "passed": passed, "attempts": attempts}),
    }

def verifier_route(state: MokioGraphState):
    if state.get('passed'):
        return 'final'
    if state.get('attempts',0) >= state.get('max_attempts',3):
        return 'final'
    return 'planner'


def get_context_token_limit():
    load_dotenv()
    raw = os.getenv("MOKIO_CONTEXT_TOKEN_LIMIT", str(DEFAULT_CONTEXT_TOKEN_LIMIT))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CONTEXT_TOKEN_LIMIT
    return value if value > 0 else DEFAULT_CONTEXT_TOKEN_LIMIT


def _context_payload(state: MokioGraphState) -> dict[str, Any]:
    return {
        "task": state.get("task", ""),
        "plan_summary": state.get("plan_summary", ""),
        "todos": state.get("todos", []),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "verification_commands": state.get("verification_commands", []),
        "research_notes": state.get("research_notes", ""),
        "sources": [
            {"title": source.get("title", ""), "url": source.get("url", "")}
            for source in state.get("sources", [])
        ],
        "agent_handoffs": state.get("agent_handoffs", []),
        "code_agent_summary": state.get("code_agent_summary", ""),
        "verifier_summary": state.get("verifier_summary", ""),
        "verification_checks": state.get("verification_checks", []),
        "last_error": state.get("last_error", ""),
        "attempts": state.get("attempts", 0),
        "max_attempts": state.get("max_attempts", 3),
        "context_summary": state.get("context_summary", ""),
        "context_next_node": state.get("context_next_node", ""),
        "compression_events": state.get("compression_events", []),
    }


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _estimate_context_tokens(state):
    messages = list(state.get("messages", []))
    payload = _context_payload(state)
    payload_message = HumanMessage(json.dumps(payload, ensure_ascii=False, default=str))
    try:
        model = create_model()
        return int(model.get_num_tokens_from_messages(messages + [payload_message]))
    except Exception:
        text = '\n'.join(_message_text(message) for message in messages)
        text += "\n" + payload_message.content
        return max(1, len(text) // 4)


def context_monitor_node(state: MokioGraphState):
    writer = _get_writer()
    token_limit = get_context_token_limit()
    token_count = _estimate_context_tokens(state)
    should_compress = token_count >= token_limit
    next_node = state.get('context_next_node', '') or 'verifier'
    event = {
        "type": "context_monitor",
        "token_count": token_count,
        "token_limit": token_limit,
        "should_compress": should_compress,
        "next_node": next_node,
        "message_count": len(state.get("messages", [])),
    }
    writer(event)
    return {
        "context_token_count": token_count,
        "context_token_limit": token_limit,
        "context_should_compress": should_compress,
        "context_next_node": next_node,
    }

def context_monitor_route(state: MokioGraphState) -> str:
    if state.get("context_should_compress"):
        return "context_compressor"
    return state.get("context_next_node") or "verifier"


def _short_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _message_snapshot(message: Any) -> dict[str, str]:
    return {
        "type": type(message).__name__,
        "name": str(getattr(message, "name", "") or ""),
        "content": _short_text(_message_text(message), 2000),
    }


def _important_files_from_state(state: MokioGraphState) -> list[str]:
    files: list[str] = []
    for command in state.get("verification_commands", []):
        files.extend(re.findall(r"[\w./\\-]+\.(?:py|html|css|js|json|md|txt)", command))
    for text in [state.get("code_agent_summary", ""), state.get("last_error", "")]:
        files.extend(re.findall(r"[\w./\\-]+\.(?:py|html|css|js|json|md|txt)", text))
    seen: set[str] = set()
    deduped = []
    for item in files:
        normalized = item.strip("\"'")
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped


def _fallback_compression(state: MokioGraphState, *, error: str = "") -> dict[str, Any]:
    return {
        "summary": _short_text(
            "\n\n".join(
                [
                    state.get("context_summary", ""),
                    state.get("research_notes", ""),
                    state.get("code_agent_summary", ""),
                    state.get("verifier_summary", ""),
                    state.get("last_error", ""),
                ]
            ),
            2400,
        ),
        "active_goal": state.get("task", ""),
        "completed_work": state.get("code_agent_summary", ""),
        "open_todos": [
            todo.get("content", "")
            for todo in state.get("todos", [])
            if todo.get("status") != "completed"
        ],
        "important_files": _important_files_from_state(state),
        "tool_findings": _short_text(state.get("last_error", ""), 1200),
        "sources": [{"title": source.get("title", ""), "url": source.get("url", "")} for source in state.get("sources", [])],
        "next_steps": state.get("context_next_node", ""),
        "risks": error,
    }


def _compress_context_with_model(state):
    payload = {
        "context_summary": state.get("context_summary", ""),
        "state": _context_payload(state),
        "messages": [_message_snapshot(message) for message in state.get("messages", [])],
    }
    messages = [
        SystemMessage(content=CONTEXT_COMPRESSION_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    try:
        response = create_model().invoke(messages)
        parsed = _extract_json(str(response.content))
        if parsed:
            return parsed
    except Exception as exc:
        return _fallback_compression(state, error=f"{type(exc).__name__}: {exc}")
    return _fallback_compression(state, error="compressor model did not return valid JSON")


def _format_compressed_context(compressed: dict[str, Any], state: MokioGraphState) -> str:
    payload = {
        "type": "mokio_context_summary",
        "task": state.get("task", ""),
        "plan_summary": state.get("plan_summary", ""),
        "todos": state.get("todos", []),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "verification_commands": state.get("verification_commands", []),
        "attempts": state.get("attempts", 0),
        "passed": state.get("passed"),
        "compression": compressed,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _trim_handoffs(handoffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trimmed = []
    for handoff in handoffs[-6:]:
        trimmed.append(
            {
                "from_agent": handoff.get("from_agent", ""),
                "to_agent": handoff.get("to_agent", ""),
                "instruction": _short_text(str(handoff.get("instruction", "")), 500),
                "result": _short_text(str(handoff.get("result", "")), 700),
            }
        )
    return trimmed


def context_compressor_node(state: MokioGraphState):
    writer = _get_writer()
    before_tokens = state.get('context_token_count') or _estimate_context_tokens(state)
    before_messages =  list(state.get("messages", []))
    compressed = _compress_context_with_model(state)
    summary = _format_compressed_context(compressed, state)
    summary_message = AIMessage(summary)
    post_state: MokioGraphState = {
        **state,
        "messages": [summary_message],
        "context_summary": summary,
        "research_notes": _short_text(state.get("research_notes", ""), 1200),
        "agent_handoffs": _trim_handoffs(state.get("agent_handoffs", [])),
        "last_error": _short_text(state.get("last_error", ""), 1600),
        "code_agent_summary": _short_text(state.get("code_agent_summary", ""), 1200),
        "verifier_summary": _short_text(state.get("verifier_summary", ""), 1200),
    }
    after_tokens = _estimate_context_tokens(post_state)
    compression_event = {
        "before_tokens": int(before_tokens),
        "after_tokens": int(after_tokens),
        "removed_messages": len(before_messages),
        "summary": _short_text(summary, 1200),
        "next_node": state.get("context_next_node", "verifier"),
    }
    events = list(state.get("compression_events", [])) + [compression_event]
    writer({"type": "context_compression", **compression_event})
    return {
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), summary_message],
        "context_summary": summary,
        "context_token_count": after_tokens,
        "context_should_compress": False,
        "research_notes": post_state.get("research_notes", ""),
        "agent_handoffs": post_state.get("agent_handoffs", []),
        "last_error": post_state.get("last_error", ""),
        "code_agent_summary": post_state.get("code_agent_summary", ""),
        "verifier_summary": post_state.get("verifier_summary", ""),
        "compression_events": events,
    }


def context_compressor_route(state: MokioGraphState) -> str:
    return state.get("context_next_node") or "verifier"


def final_node(state: MokioGraphState) -> dict[str, Any]:
    status = "PASSED" if state.get("passed") else "FAILED"
    checks = "\n".join(
        f"- {check.get('name', 'check')}: {'PASS' if check.get('passed') else 'FAIL'} - {check.get('detail', '')}"
        for check in state.get("verification_checks", [])
    )
    todos = "\n".join(f"- [{todo.get('status', '')}] {todo.get('content', '')}" for todo in state.get("todos", []))
    sources = "\n".join(f"- {source.get('title', '')}: {source.get('url', '')}" for source in state.get("sources", []))
    compression_events = state.get("compression_events", [])
    compression_text = "(none)"
    if compression_events:
        latest = compression_events[-1]
        compression_text = (
            f"{len(compression_events)} compression(s); "
            f"latest {latest.get('before_tokens')} -> {latest.get('after_tokens')} tokens; "
            f"removed {latest.get('removed_messages')} message(s)"
        )
    final_answer = (
        f"LangGraph MultiAgent workflow finished: {status}\n\n"
        f"Plan: {state.get('plan_summary', '')}\n\n"
        f"Todos:\n{todos}\n\n"
        f"Research sources:\n{sources or '(none)'}\n\n"
        f"Verifier:\n{state.get('verifier_summary', '')}\n\n"
        f"Checks:\n{checks or '(none)'}\n\n"
        f"Context compression:\n{compression_text}\n\n"
        f"CodeAgent summary:\n{state.get('code_agent_summary') or state.get('last_actor_summary', '')}"
    )
    return {"final_answer": final_answer}