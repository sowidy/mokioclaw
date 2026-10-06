from mokioclaw.tools.grep_tool import grep

from langchain_core.tools import StructuredTool

from mokioclaw.core.state import RuntimeState
from mokioclaw.tools.file_tools import read_file,write_file,edit_file
from mokioclaw.tools.bash_tool import run_bash, bash_tool_description
from mokioclaw.tools.todo_tool import update_todo
from mokioclaw.tools.web_search_tool import build_web_search_tool


def build_tools(state: RuntimeState):
    """
    使用langchain工具注册类快速注册工具
    :param state:
    :return:
    """
    return [
        StructuredTool.from_function(
            name='FileReadTool',
            func= lambda file_path, offset=0, limit=2000: read_file(state, file_path, offset, limit),
            description="Read a UTF-8 text file inside the workspace. Supports offset and limit."
        ),
        StructuredTool.from_function(
            name='FileWriteTool',
            func= lambda file_path, data: write_file(state,file_path, data),
            description="Create a new file or rewrite an existing file inside the workspace."
        ),
        StructuredTool.from_function(
            name='FileEditTool',
            func= lambda file_path, old_text, new_text: edit_file(state, file_path, old_text, new_text),
            description="Edit an existing file or rewrite an existing file inside the workspace."
        ),
        StructuredTool.from_function(
            name='GrepTool',
            func= lambda pattern, path='.', glob=None,  head_limit=50, ignore_case=False: grep(
                state, pattern, path, glob, head_limit, ignore_case
            ),
            description="Search workspace text files by regex pattern and return matching lines."
        ),
        StructuredTool.from_function(
            name="BashTool",
            func= lambda command, timeout_seconds=10: run_bash(state, command, timeout_seconds),
            description="Run a safe development shell command inside the workspace with timeout and output capture."
        )
    ]

def _build_todo_update_tool(todos: list[dict[str, str]]):
    return StructuredTool.from_function(
        name='TodoUpdateTool',
        func=lambda todo_id, status,note: update_todo(todos, todo_id, status, note),
        description="Update one existing todo status. Args: todo_id, status, optional note."
    )

def build_read_only_tools(state: RuntimeState) -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            name="FileReadTool",
            func=lambda file_path, offset=0, limit=2000: read_file(state, file_path, offset, limit),
            description="Read a UTF-8 text file inside the workspace. Supports offset and limit.",
        ),
        StructuredTool.from_function(
            name="GrepTool",
            func=lambda pattern, path=".", glob=None, head_limit=50, ignore_case=False: grep(
                state, pattern, path, glob, head_limit, ignore_case
            ),
            description="Search workspace text files by regex pattern and return matching lines.",
        ),
        StructuredTool.from_function(
            name="BashTool",
            func=lambda command, timeout_seconds=10: run_bash(state, command, timeout_seconds),
            description=bash_tool_description(),
        ),
        build_web_search_tool(),
    ]