import json
from typing import Callable, Any
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from mokioclaw.agents.search_agent import _last_ai_content
from mokioclaw.graph.state import MokioGraphState
from mokioclaw.prompts.stage3 import CODE_AGENT_PROMPT
from mokioclaw.providers.model_provider import create_model
from mokioclaw.tools import build_tools
from mokioclaw.tools.registry import _build_todo_update_tool
from mokioclaw.tools.todo_tool import update_todo

Writer = Callable[[dict[str, Any]], None] # ?


def _code_agent_input(state: MokioGraphState, instruction: str, todos: list[dict[str, Any]]):
    todo_text= '\n'.join(f"- {todo['id']} [{todo['status']} [{todo['note']}]" for todo in todos)
    criteria_text = '\n'.join(f"- {item}" for item in state.get('acceptance_criteria', []))
    command_text = "\n".join(f"- {command}" for command in state.get("verification_commands", []))
    source_text = "\n".join(f"- {source.get('title',"")}:{source.get("url","")}" for source in state.get("sources", []))

    return (
        f"Task: {state['task']}\n\n"
        f"Planner instruction:\n{instruction}\n\n"
        f"Plan: {state.get('plan_summary', '')}\n\n"
        f"Todos:\n{todo_text}\n\n"
        f"Acceptance criteria:\n{criteria_text}\n\n"
        f"Verification commands:\n{command_text}\n\n"
        f"Research notes:\n{state.get('research_notes', '')}\n\n"
        f"Sources:\n{source_text}\n\n"
        f"Previous verifier failure:\n{state.get('last_error', '')}"
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
    max_loop: int = 4
):
    runtime = state.get('runtime')
    todos = [dict(todo) for todo in state.get('todos', [])]
    writer = writer or (lambda _: None)
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
        HumanMessage(
            _code_agent_input(state, instruction, todos)
        )
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