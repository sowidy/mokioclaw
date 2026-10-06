import os

from dotenv import load_dotenv
from langchain_core.tools import StructuredTool


def _coerce_bool(value:bool | str):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "no", "0","off"}

def web_search(
        query:str,
        max_results:int | str =5,
        include_answer:bool | str=True,
):
    load_dotenv()
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return {
            'ok': False,
            'error':"missing required .env setting: TAVILY_API_KEY"
        }
    try:
        from tavily import TavilyClient
    except ModuleNotFoundError as e:
        return {
            "ok": False,
            "error": f"tavily-python is not installed: {e}"
        }
    try:
        max_value = int(max_results)
    except (TypeError, ValueError):
        max_value = 5
    max_value = max(1, min(max_value, 10))
    answer_value = _coerce_bool(include_answer)
    try:
        tavily_client = TavilyClient(api_key=api_key)
        response = tavily_client.search(query=query, max_results=max_value, include_answer=answer_value,search_depth="basic")
    except Exception as e:
        return {
            "ok": False,
            "query": query,
            "error": f"{type(e).__name__}: {e}"
        }
    results = []
    for item in response.get("results",[]) or []:
        results.append({
            'title': str(item.get("title","")),
            'url': str(item.get("url")),
            'content': str(item.get("content",""))[:1200],
            'score': item.get("score",0),
        })

    return {
        'ok': True,
        'query': query,
        'answers': response.get("answers","") or "",
        'results': results
    }


def build_web_search_tool():
    return StructuredTool.from_function(
        name="web_search",
        func=web_search,
        description=(
            "Search the web with Tavily. Args: query, optional max_results, optional include_answer. "
            "Returns answer and result sources with title, url, content, and score."
        ),
    )