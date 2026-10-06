import sys
from pathlib import Path

import typer
from typing import Annotated

from mokioclaw.cli.formatter import safe_echo, safe_secho, print_event
from mokioclaw.core.agent import stream_agent_events

app = typer.Typer(
    help="mokioclaw: a teaching-first mini CodeAgent.",
    context_settings={"allow_interspersed_args": True},
)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


@app.callback(invoke_without_command=True)
def main(
        ctx: typer.Context,
        task:Annotated[str|None, typer.Argument(help="Natural-language task for the CodeAgent.")] = None,
        workplace: Annotated[
            Path | None,
            typer.Option("--workplace", "-w", help="Workspace for generated files. Defaults to .mokioclaw/workspace."),
        ] = None,
        max_attempts: Annotated[
            int,
            typer.Option("--max-attempts", help="Maximum planner/actor/verifier attempts before finalizing."),
        ] = 3
):
    if ctx.invoked_subcommand is not None:
        return
    configure_console()
    if not task:
        safe_echo(ctx.get_help())
        raise typer.Exit()
    # safe_secho("mokioclaw stage 1: create_agent ReAct loop", fg=typer.colors.MAGENTA)
    # safe_secho("mokioclaw stage 2: LangGraph planner -> actor -> verifier", fg=typer.colors.MAGENTA)
    safe_secho("mokioclaw stage 4: MultiAgent + context compression", fg=typer.colors.MAGENTA)
    for event in stream_agent_events(task, workplace=workplace,max_attempts=max_attempts):
        print_event(event)