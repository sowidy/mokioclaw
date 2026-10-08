import json
from typing import Callable, Any
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from mokioclaw.agents.search_agent import _last_ai_content
from mokioclaw.graph.memory import build_layered_memory, memory_event, format_layered_memory_for_prompt
from mokioclaw.graph.state import MokioGraphState
from mokioclaw.prompts.stage3 import CODE_AGENT_PROMPT
from mokioclaw.providers.model_provider import create_model
from mokioclaw.tools import build_tools
from mokioclaw.tools.notepad_tool import read_notepad
from mokioclaw.tools.registry import _build_todo_update_tool
from mokioclaw.tools.todo_tool import update_todo, persist_todos

Writer = Callable[[dict[str, Any]], None] # ?


def _code_agent_input(state: MokioGraphState, instruction: str, memory: dict[str, Any]) -> str:
    return (
        f"Task: {state['task']}\n\n"
        f"Planner instruction:\n{instruction}\n\n"
        "Layered memory snapshot:\n"
        f"{format_layered_memory_for_prompt(memory)}"
    )


def _execute_code_agent_tool(runtime: MokioGraphState, todos: list[dict[str, Any]],call:list[dict[str, Any]]):
    name = call["name"]
    args = call["args"] or {}
    if name in ("TodoUpdateTool", "TodoUpdate"):
        result = update_todo(todos, args.get('todo_id',""), args.get('status',""), args.get('note',""))
        if result.get("ok"):
            todos = result.get("todos", [])
    else :
        tools = {tool.name:tool for tool in build_tools(runtime)}
        tool = tools.get(name)
        if tool is None and not name.endswith("Tool"):
            tool = tools.get(name + "Tool")
        if tool is None:
            result = {
                'ok': False,
                "error": f"unknown tool: {name}"
            }
        else:
            try:
                result = tool.invoke(args)
            except Exception as e:
                result = {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}"
                }
    tool_call_id = call.get("id") or f"{name}-call"
    return ToolMessage(
        content=json.dumps(result, ensure_ascii=False),
        name=name,
        tool_call_id=tool_call_id
    ), todos


def _tool_result_event(tool_message: ToolMessage, node: str):
    try:
        parsed = json.loads(str(tool_message.content))
    except json.decoder.JSONDecodeError:
        parsed = tool_message.content
    return {
        "type": "tool_result",
        "node": node,
        "name": tool_message.name,
        "result": parsed
    }


def run_code_agent(
    state: MokioGraphState,
    instruction: str,
    *,
    writer: Writer | None = None,
    max_loop: int = 8
):
    runtime = state.get('runtime')
    todos = [dict(todo) for todo in state.get('todos', [])]
    writer = writer or (lambda _: None)
    memory = build_layered_memory({**state, "todos": todos}, node="codeAgent") # 得到work和history的记忆
    writer(memory_event(memory, node="codeAgent"))
    model = create_model()
    code_agent = model.bind_tools(build_tools(runtime) + [_build_todo_update_tool(todos)])

    writer({
        "type": "plan_snapshot",
        "node": "codeAgent",
        "plan_summary": state.get("plan_summary", ""),
        "todos": todos,
        "verification_commands": state.get("verification_commands", []),
    })
    messages = [
        SystemMessage(CODE_AGENT_PROMPT),
        HumanMessage(content=_code_agent_input(state, instruction, memory)),
    ]
    produced_messages:list[Any] = []
    tool_events:list[dict[str, Any]] = []


    for _ in range(max_loop):
        response = code_agent.invoke(messages)
        produced_messages.append(response)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break
        for call in tool_calls:
            writer({
                'type': "tool_call",
                'node': 'codeAgent',
                'name': call["name"],
                'args': call["args"],
            })
            tool_result, todos = _execute_code_agent_tool(runtime, todos, call)
            event = _tool_result_event(tool_result, node='codeAgent')
            tool_events.append(event)
            writer(event)
            if call.get("name") == "TodoUpdateTool":
                persist_todos(
                    runtime,
                    todos,
                    state.get("acceptance_criteria", []),
                    state.get("verification_commands", []),
                    state.get("plan_summary", ""),
                )
                writer(
                    {
                        "type": "todo_update",
                        "node": "codeAgent",
                        "plan_summary": state.get("plan_summary", ""),
                        "todos": todos,
                        "verification_commands": state.get("verification_commands", []),
                    }
                )
            produced_messages.append(tool_result)
            messages.append(tool_result)
    else:
        produced_messages.append(
            AIMessage(content="codeAgent stopped after the maximum tool loop count; verifier will inspect current files.")
        )
    summary = _last_ai_content(produced_messages)
    return {
        "ok": True,
        "summary": summary,
        "todos": todos or state.get("todos", []),
        "messages": produced_messages,
        "tool_events": tool_events,
    }