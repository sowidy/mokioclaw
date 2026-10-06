from typing import TypedDict, Any, Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages

from mokioclaw.core.state import RuntimeState


class TodoItem(TypedDict):
    id:str
    content:str
    status:str
    note:str


class VerificationResult(TypedDict):
    command:str
    ok:bool
    exit_code:int | None
    stdout:str
    stderr:str

class SourceItem(TypedDict):
    title:str
    url:str
    content:str
    score:float

class AgentHandoff(TypedDict):
    from_agent:str
    to_agent:str
    instructions:str
    result:str

class VerificationCheck(TypedDict):
    name:str
    passed:bool
    detail:str

class CompressionEvent(TypedDict):
    before_token:str
    after_token:str
    remove_messages:str
    summary:str
    next_node:str

class MokioGraphState(TypedDict):
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

    research_note:str
    sources:list[SourceItem]
    agent_handoffs:list[AgentHandoff]
    code_agent_summary:str
    verifier_summary:str
    verification_checks:list[VerificationCheck]
    context_summary:str
    context_token_summary:int
    context_token_limit:int
    context_should_compress:bool
    context_next_node:str
    compression_events:list[CompressionEvent]
