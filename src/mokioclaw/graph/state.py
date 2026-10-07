from typing import TypedDict, Any, Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages

from mokioclaw.core.state import RuntimeState


class TodoItem(TypedDict,total=False):
    id:str
    content:str
    status:str
    note:str


class VerificationResult(TypedDict,total=False):
    command:str
    ok:bool
    exit_code:int | None
    stdout:str
    stderr:str

class SourceItem(TypedDict,total=False):
    title:str
    url:str
    content:str
    score:float

class AgentHandoff(TypedDict,total=False):
    from_agent:str
    to_agent:str
    instructions:str
    result:str

class VerificationCheck(TypedDict,total=False):
    name:str
    passed:bool
    detail:str

class CompressionEvent(TypedDict,total=False):
    before_token:str
    after_token:str
    remove_messages:str
    summary:str
    next_node:str


class LayeredMemory(TypedDict,total=False):
    rule: dict[str, Any]
    work_memory: dict[str, Any]
    history_summary_store: dict[str, Any]


class MokioGraphState(TypedDict,total=False):
    task:str
    runtime: RuntimeState
    messages:Annotated[list[BaseMessage], add_messages]
    plan_summary:str
    todos:list[TodoItem]
    acceptance_criteria:list[str]
    verification_commands:list[str]
    verification_results:list[VerificationResult]
    passed:bool
    attempts:int
    max_attempts:int
    final_answer:str
    last_actor_summary:str
    last_error:str
    metadata:dict[str, Any]

    research_notes:str
    sources:list[SourceItem]
    agent_handoffs:list[AgentHandoff]
    code_agent_summary:str
    verifier_summary:str
    verification_checks:list[VerificationCheck]
    context_summary:str
    context_token_count:int
    context_token_limit:int
    context_should_compress:bool
    context_next_node:str
    compression_events:list[CompressionEvent]

    memory_snapshot:dict[str, Any]
    history_summary: str
