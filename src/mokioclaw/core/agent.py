from pathlib import Path
from typing import Any

from mokioclaw.core.paths import default_project_root
from mokioclaw.core.state import RuntimeState
from mokioclaw.graph.workflow import build_workflow


def create_runtime(workplace: Path | None = None) -> RuntimeState:
    selected = workplace or default_project_root()
    selected.mkdir(parents=True, exist_ok=True)
    return RuntimeState(workspace=selected)

# def create_code_agent(state: RuntimeState):
#     model = create_model()
#     return create_agent(
#         model = model,
#         tools=build_tools(state),
#         system_prompt=STAGE1_SYSTEM_PROMPT
#     )

def stream_agent_events(task: str, * , workplace: Path | None = None,max_attempts: int =3) -> Any:
    state = create_runtime(workplace)
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

