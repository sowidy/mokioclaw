import sys
from pathlib import Path

import typer
from typing import Annotated, Literal

from rich import box
from rich.panel import Panel

from mokioclaw.cli.formatter import safe_echo, safe_secho, print_event
from mokioclaw.core.agent import stream_agent_events
from mokioclaw.core.approval import ApprovalRequest, ApprovalDecision

app = typer.Typer(
    help="mokioclaw: a teaching-first mini CodeAgent.",
    context_settings={"allow_interspersed_args": True},
)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _inline_approval_handler(request: ApprovalRequest) -> ApprovalDecision:
    from mokioclaw.cli.formatter import console

    console.print(
        Panel(
            f"Command:\n{request.command}\n\nRisk:\n{request.risk_reason}",
            title=f"Human Approval · {request.tool_name}",
            border_style="yellow",
            box=box.ROUNDED,
        )
    )
    answer = typer.prompt("Approve? [y/N]", default="n", show_default=False).strip().lower()
    console.print() # 换行
    approved = answer in {"y", "yes"}
    return ApprovalDecision(approved=approved, reason="" if approved else "Rejected by human operator.")


@app.callback(invoke_without_command=True)
def main(
        ctx: typer.Context,
        task:Annotated[str|None, typer.Argument(help="Natural-language task for the CodeAgent.")] = None,
        workplace: Annotated[
            Path | None,
            typer.Option("--workplace", "-w", help="Workspace for generated files. Defaults to a fresh .mokioclaw/workspace/workspace-* directory."),
        ] = None,
        max_attempts: Annotated[
            int,
            typer.Option("--max-attempts", help="Maximum planner/actor/verifier attempts before finalizing."),
        ] = 3,
        approval_mode: Annotated[
            Literal["inline", "auto", "deny"],
            typer.Option("--approval-mode",
                         help="Human approval mode for high-risk BashTool commands: inline, auto, or deny."),
        ] = "inline",
        checkpoint_mode: Annotated[
            Literal["light", "strict", "off"],
            typer.Option("--checkpoint-mode", help="Checkpoint mode: light, strict, or off."),
        ] = "light",
        resume: Annotated[
            Path | None,
            typer.Option("--resume", help="Resume from an existing MokioClaw workspace."),
        ] = None,
):
    if ctx.invoked_subcommand is not None:
        return
    configure_console()
    if not task and resume is None:
        safe_echo(ctx.get_help())
        raise typer.Exit()
    # safe_secho("mokioclaw stage 1: create_agent ReAct loop", fg=typer.colors.MAGENTA)
    # safe_secho("mokioclaw stage 2: LangGraph planner -> actor -> verifier", fg=typer.colors.MAGENTA)
    # safe_secho("mokioclaw stage 4: MultiAgent + context compression", fg=typer.colors.MAGENTA)
    safe_secho("mokioclaw stage 5: MultiAgent + context/harness engineering", fg=typer.colors.MAGENTA)
    approval_handler = _inline_approval_handler if approval_mode == "inline" else None
    for event in stream_agent_events(
        task,
        workplace=workplace,
        max_attempts=max_attempts,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
        checkpoint_mode=checkpoint_mode,
        resume_workspace=resume,
    ):
        print_event(event)
