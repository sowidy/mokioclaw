import json
from typing import Callable, Any

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from mokioclaw.graph.state import MokioGraphState
from mokioclaw.prompts.stage3 import SEARCH_AGENT_PROMPT
from mokioclaw.providers.model_provider import create_model
from mokioclaw.tools.web_search_tool import build_web_search_tool

Writer = Callable[[dict[str, Any]], None] # ?


def _execute_search_tool(call : dict[str, Any]):
    tool = build_web_search_tool()
    name = call["name"]
    args = call["args"] or {}
    if name != tool.name:
        result = {'ok': False, 'error': f"unknown tool: {name}"}
    else:
        try:
            result = tool.invoke(args)
        except Exception as e:
            result = {'ok': False, 'error': f"{type(e).__name__}: {e}"}
    return ToolMessage(
        content=json.dumps(result, ensure_ascii=False),
        name=name,
        tool_call_id=call.get('id') or f'{name}-call',
    )


def _tool_result_event(tool_message: dict[str, Any]):
    parsed = _pares_tool_content(tool_message.content)
    return {
        'type':"tool_result",
        'node':'search_agent',
        'name':tool_message.name,
        'result':parsed,
    }


def _pares_tool_content(content:Any):
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def _last_ai_content(messages:list[Any]):
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        content = getattr(message, 'content', "")
        if content:
            return str(content)
    return ""


def _dedupe_sources(sources:list[dict[str, Any]]):
    """
    去重
    :param sources:
    :return:
    """
    seen :set[str] = set()
    dedupe = []
    for s in sources:
        url = str(s.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        dedupe.append(s)
    return dedupe



def run_search_agent(
        state: MokioGraphState,
        instruction:str,
        *,
        writer:Writer|None = None,
        max_loop:int = 4
):
    writer = writer or (lambda _: None)
    model = create_model()
    search_agent = model.bind_tools([build_web_search_tool()])
    messages = [
        SystemMessage(content=SEARCH_AGENT_PROMPT),
        HumanMessage(
            f"task:{state.get("task")}\n"
            f"Plan instruction:{instruction}\n"
            f"Existing research notes:\n{state.get('research_notes', '')}\n"
            "Search as needed and finish with a concise research summary plus source URLs."
        ),
    ]

    produced_messages:list[Any] = []
    queried :list[str] = []
    sources :list[dict[str, Any]] = []
    answers :list[str] = []
    tool_events:list[dict[str, Any]] = []

    for _ in range(max_loop):
        response = search_agent.invoke(messages)
        produced_messages.append(response)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break
        for call in tool_calls:
            args = call["args"] or {}
            query = str(args.get("query", ""))
            if query:
                queried.append(query)
            writer({
                'type':"tool_call",
                'node':'search_agent',
                'name':call["name"],
                'args':args,
            })
            tool_result = _execute_search_tool(call)
            event = _tool_result_event(tool_result)
            tool_events.append(event)
            writer(event)
            parsed = _pares_tool_content(tool_result.content)
            if isinstance(parsed, dict):
                if parsed.get("answer"):
                    answers.append(parsed["answer"])
                for item in parsed.get("results", []) or []:
                    if isinstance(item, dict):
                        sources.append(item)
                writer({
                    'type':"search_results",
                    'query':parsed.get("query", query),
                    'answer':parsed.get("answers", ""),
                    'sources':parsed.get("results", []),
                })
            produced_messages.append(tool_result)
            messages.append(tool_result)
    summary = _last_ai_content(produced_messages) or "\n".join(answers)
    result = {
        'ok':True,
        'summary':summary,
        'queries':queried,
        'sources':_dedupe_sources(sources),
        'messages':produced_messages,
        'tool_events':tool_events,
    }
    writer(
        {
            "type": "search_summary",
            "summary": result["summary"],
            "queries": result["queries"],
            "sources": result["sources"],
        }
    )
    return result