from typing import List, TypedDict

from langgraph.graph import END, StateGraph

from agents.architect import architect_node
from agents.commenter import commenter_node
from tools.ingester import ingest_to_chroma
from tools.scanner import get_codebase_map
from utils.storage import project_namespace, write_status


class GraphState(TypedDict):
    user_id: str
    project_name: str
    project_path: str
    unprocessed_files: List[str]
    processed_files: List[str]
    comment_report: list
    errors: List[str]


def scanner_node(state: GraphState):
    write_status(state["user_id"], state["project_name"], "processing", "scan", "Scanning codebase...", 15)
    try:
        scan_result = get_codebase_map(
            state["project_path"],
            user_id=state["user_id"],
            project_name=state["project_name"],
        )
        return {
            "unprocessed_files": scan_result["files_to_process"],
            "processed_files": [],
            "comment_report": [],
            "errors": [],
        }
    except Exception as exc:
        return {"errors": [f"Scanner failed: {exc}"]}


def wrapped_commenter_node(state: GraphState):
    unprocessed = len(state.get("unprocessed_files", []))
    processed = len(state.get("processed_files", []))
    total = max(unprocessed + processed, 1)
    progress = 20 + int((processed / total) * 35)
    write_status(
        state["user_id"],
        state["project_name"],
        "processing",
        "comment",
        f"Commenting files ({processed}/{total})...",
        progress,
    )
    return commenter_node(state)


def wrapped_architect_node(state: GraphState):
    write_status(state["user_id"], state["project_name"], "processing", "diagram", "Generating diagrams...", 65)
    return architect_node(state)


def ingestion_node(state: GraphState):
    write_status(state["user_id"], state["project_name"], "processing", "embed", "Embedding chunks into ChromaDB...", 82)
    isolated_name = project_namespace(state["user_id"], state["project_name"])
    processed = state.get("processed_files", [])
    if processed:
        ingest_to_chroma(
            processed,
            isolated_name,
            project_root=state["project_path"],
            user_id=state["user_id"],
        )
    write_status(state["user_id"], state["project_name"], "ready", "complete", "Project is ready for chat.", 100)
    return state


def route_commenting(state: GraphState):
    if state.get("errors"):
        write_status(
            state["user_id"],
            state["project_name"],
            "error",
            "scan",
            "Ingestion failed.",
            0,
            "; ".join(state.get("errors", [])),
        )
        return "end"
    if state.get("unprocessed_files"):
        return "commenter"
    return "architect"


workflow = StateGraph(GraphState)
workflow.add_node("scanner", scanner_node)
workflow.add_node("commenter", wrapped_commenter_node)
workflow.add_node("architect", wrapped_architect_node)
workflow.add_node("ingestion", ingestion_node)
workflow.add_node("end", lambda state: state)

workflow.set_entry_point("scanner")
workflow.add_conditional_edges("scanner", route_commenting, {"commenter": "commenter", "architect": "architect", "end": "end"})
workflow.add_conditional_edges("commenter", route_commenting, {"commenter": "commenter", "architect": "architect", "end": "end"})
workflow.add_edge("architect", "ingestion")
workflow.add_edge("ingestion", END)
workflow.add_edge("end", END)

codegraph_app = workflow.compile()
