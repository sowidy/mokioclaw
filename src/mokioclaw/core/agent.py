import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from mokioclaw.core.paths import default_workplace
from mokioclaw.core.state import RuntimeState
from mokioclaw.graph.workflow import build_workflow

def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser() if raw else None

def create_runtime(
    workplace: Path | None = None,
    *,
    approval_mode: str = "inline",
    approval_handler=None,
) -> RuntimeState:
    load_dotenv()
    selected = workplace or default_workplace() # 每个任务都有一个独立的workplace
    selected.mkdir(parents=True, exist_ok=True)
    return RuntimeState(
        workspace=selected,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
        bash_default_timeout_seconds=_env_int("MOKIO_BASH_DEFAULT_TIMEOUT_SECONDS", 120),
        bash_max_timeout_seconds=_env_int("MOKIO_BASH_MAX_TIMEOUT_SECONDS", 600),
        bash_max_output_chars=_env_int("MOKIO_BASH_MAX_OUTPUT_CHARS", 6000),
        bash_env_file=_env_path("MOKIO_BASH_ENV_FILE"),
    )

# def create_code_agent(state: RuntimeState):
#     model = create_model()
#     return create_agent(
#         model = model,
#         tools=build_tools(state),
#         system_prompt=STAGE1_SYSTEM_PROMPT
#     )

def stream_agent_events(
    task: str,
    * ,
    workplace: Path | None = None,
    max_attempts: int =3,
    approval_mode: str = "inline",
    approval_handler=None,
) -> Any:
    state = create_runtime(workplace,approval_mode=approval_mode, approval_handler=approval_handler)
    workflow = build_workflow()
    yield {'type':'workplace','path': str(state.workspace)}

    # inputs = {
    #     'messages': [HumanMessage(content=task)],
    # }
    inputs: dict[str, Any] = {
        "task": task,
        "runtime": state,
        "messages": [],
        "attempts": 0,
        "max_attempts": max_attempts,
    }
    for mode, event in workflow.stream(inputs, stream_mode=["updates", "custom"]):
        if mode == "updates":
            yield {'type': 'agent_event', 'value': event}
        else:  # mode == "custom"
            yield {'type': 'custom_event', 'value': event}

