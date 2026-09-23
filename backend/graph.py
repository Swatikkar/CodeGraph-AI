import json
from typing import List, TypedDict

from langgraph.graph import END, StateGraph

from agents.architect import architect_node
from agents.commenter import commenter_node
from config import settings
from tools.ingester import ingest_to_chroma
from tools.scanner import get_codebase_map
from utils.storage import ingestion_report_path, project_namespace, write_status
from utils.job_control import raise_if_cancelled


class GraphState(TypedDict):
    job_id: int
    user_id: str
    project_name: str
    project_path: str
    unprocessed_files: List[str]
    processed_files: List[str]
    comment_report: list
    scanned_files: int
    errors: List[str]


def write_ingestion_report(state: GraphState, **extra):
    report_path = ingestion_report_path(state["user_id"], state["project_name"])
    existing = {}
    if report_path.exists():
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    payload = {
        **existing,
        "scanned_files": state.get("scanned_files", existing.get("scanned_files", 0)),
        "processed_files": len(state.get("processed_files", [])),
        "remaining_files": len(state.get("unprocessed_files", [])),
        "comment_report": state.get("comment_report", []),
        **extra,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def scanner_node(state: GraphState):
    raise_if_cancelled(state.get("job_id"))
    write_status(state["user_id"], state["project_name"], "processing", "scan", "Scanning codebase...", 15)
    try:
        scan_result = get_codebase_map(
            state["project_path"],
            user_id=state["user_id"],
            project_name=state["project_name"],
        )
        scanned_files = scan_result["total_files"]
        print(
            f"[ingestion] scanned {scanned_files} files for "
            f"{state['user_id']}/{state['project_name']} "
            f"selected_bytes={scan_result['selected_bytes']} "
            f"skipped={scan_result['skipped_files']} "
            f"skip_reasons={scan_result['skip_reasons']}",
            flush=True,
        )
        write_ingestion_report(
            {**state, "scanned_files": scanned_files},
            scanned_file_paths=scan_result["files_to_process"],
            stage="scan",
        )
        return {
            "unprocessed_files": scan_result["files_to_process"],
            "processed_files": [],
            "comment_report": [],
            "scanned_files": scanned_files,
            "errors": [],
        }
    except Exception as exc:
        return {"errors": [f"Scanner failed: {exc}"]}


def wrapped_commenter_node(state: GraphState):
    raise_if_cancelled(state.get("job_id"))
    unprocessed = len(state.get("unprocessed_files", []))
    processed = len(state.get("processed_files", []))
    total = max(unprocessed + processed, 1)
    progress = 20 + int((processed / total) * 35)
    stage_message = (
        f"Commenting files ({processed}/{total})..."
        if settings.ENABLE_LLM_CODE_COMMENTING
        else f"Preparing source files ({processed}/{total})..."
    )
    write_status(
        state["user_id"],
        state["project_name"],
        "processing",
        "comment",
        stage_message,
        progress,
    )
    result = commenter_node(state)
    write_ingestion_report({**state, **result}, stage="comment")
    return result


def wrapped_architect_node(state: GraphState):
    raise_if_cancelled(state.get("job_id"))
    write_status(state["user_id"], state["project_name"], "processing", "diagram", "Generating diagrams...", 65)
    return architect_node(state)


def ingestion_node(state: GraphState):
    raise_if_cancelled(state.get("job_id"))
    write_status(state["user_id"], state["project_name"], "processing", "embed", "Embedding chunks into ChromaDB...", 82)
    isolated_name = project_namespace(state["user_id"], state["project_name"])
    processed = state.get("processed_files", [])
    if processed:
        embedded_chunks = ingest_to_chroma(
            processed,
            isolated_name,
            project_root=state["project_path"],
            user_id=state["user_id"],
        )
    else:
        embedded_chunks = 0
    raise_if_cancelled(state.get("job_id"))
    write_ingestion_report(state, stage="embed", embedded_chunks=embedded_chunks)
    write_status(
        state["user_id"],
        state["project_name"],
        "processing",
        "persist",
        "Publishing the completed project...",
        95,
    )
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
