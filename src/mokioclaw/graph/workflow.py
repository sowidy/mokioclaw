from pathlib import Path

from langgraph.constants import START, END
from langgraph.graph import StateGraph

from mokioclaw.graph.nodes import (
    context_compressor_node,
    context_compressor_route,
    context_monitor_node,
    context_monitor_route,
    final_node,
    planner_node,
    verifier_node,
)
from mokioclaw.graph.state import MokioGraphState


def _save_workflow_diagram(graph, path: str = "workflow.png") -> None:
    """Render the compiled graph to a PNG (Mermaid) and save it.

    PNG rendering needs the `pyppeteer` package; if it's missing, fall back to
    saving the Mermaid source as a `.mmd` file instead.
    """
    try:
        graph.get_graph().draw_mermaid_png(output_file_path=path)
        print(f"[workflow] diagram saved to {path}")
    except Exception as exc:
        mmd_path = str(Path(path).with_suffix(".mmd"))
        try:
            mermaid = graph.get_graph().draw_mermaid()
            Path(mmd_path).write_text(mermaid, encoding="utf-8")
            print(
                f"[workflow] PNG render failed ({type(exc).__name__}: {exc}); "
                f"saved Mermaid source to {mmd_path}. "
                f"Install `pyppeteer` to get PNG output."
            )
        except Exception as exc2:
            print(f"[workflow] diagram render failed: {type(exc2).__name__}: {exc2}")


def build_workflow():
    builder = StateGraph(MokioGraphState)
    builder.add_node("planner", planner_node)
    builder.add_node("context_compressor", context_compressor_node)
    builder.add_node("context_monitor", context_monitor_node)
    builder.add_node("verifier", verifier_node)
    builder.add_node("final", final_node)


    builder.add_edge(START, "planner")
    builder.add_edge("planner", "context_monitor")
    builder.add_conditional_edges("context_monitor",context_monitor_route,path_map={
        "context_compressor": "context_compressor",
        "verifier": "verifier",
        "planner": "planner",
        "final": "final"
    })
    builder.add_conditional_edges("context_compressor",context_compressor_route,path_map={
        "verifier": "verifier",
        "planner": "planner",
        "final": "final"
    })
    builder.add_edge("verifier", "context_monitor")
    builder.add_edge("final", END)



    graph = builder.compile()
    _save_workflow_diagram(graph)
    return graph