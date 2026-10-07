from typing import Any


def _short_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


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
