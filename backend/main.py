import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from config import settings
from graph import codegraph_app
from graph_chat import chat_graph_app
from providers import model_router
from tools.file_ops import write_project_file_after_approval
from tools.ingester import purge_project_from_chroma
from tools.retriever import invalidate_vectorstore_cache
from utils.auth import auth_router, get_current_user
from utils.guardrails import screen_user_prompt, validate_image_upload, validate_zip_upload
from utils.storage import (
    architecture_path,
    delete_project_artifacts,
    dependency_graph_path,
    ensure_project_dirs,
    project_namespace,
    project_root,
    read_status,
    resolve_project_file,
    slugify_project_name,
    tree_cache_path,
    user_upload_root,
    write_status,
)


settings.configure_langsmith()
settings.create_required_directories()

app = FastAPI(title=settings.APP_NAME, version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)

_vision_executor = ThreadPoolExecutor(max_workers=2)


class GitRepoPayload(BaseModel):
    repo_url: str
    project_name: str


class ChatPayload(BaseModel):
    project_name: str
    query: str
    thread_id: str
    is_approval: bool = False
    approval_file_path: str | None = None
    approval_new_content: str | None = None


def sse_event(event_type: str, **payload) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def run_pipeline_in_background(user_id: str, project_name: str):
    try:
        state = {
            "user_id": str(user_id),
            "project_name": project_name,
            "project_path": str(project_root(user_id, project_name)),
            "unprocessed_files": [],
            "processed_files": [],
            "comment_report": [],
            "errors": [],
        }
        codegraph_app.invoke(state)
    except Exception as exc:
        write_status(user_id, project_name, "error", "pipeline", "Background analysis failed.", 0, str(exc))


def safe_extract_zip(zip_path: Path, destination: Path):
    destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            target = (destination / member_path).resolve()
            if member_path.is_absolute() or not target.is_relative_to(destination):
                raise HTTPException(status_code=400, detail="ZIP archive contains an unsafe file path.")
        archive.extractall(destination)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME, "environment": settings.ENVIRONMENT}


@app.post("/api/process-zip")
async def process_zip_upload(
    background_tasks: BackgroundTasks,
    project_name: str = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    await validate_zip_upload(file)
    slug = slugify_project_name(project_name)
    ensure_project_dirs(current_user.id, slug)
    root = project_root(current_user.id, slug)
    zip_path = user_upload_root(current_user.id) / f"{slug}.zip"

    try:
        if root.exists():
            shutil.rmtree(root)
        with zip_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        safe_extract_zip(zip_path, root)
        zip_path.unlink(missing_ok=True)
        write_status(current_user.id, slug, "processing", "extract", f"Project '{slug}' uploaded.", 5)
    except HTTPException:
        zip_path.unlink(missing_ok=True)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        delete_project_artifacts(current_user.id, slug)
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to handle file upload: {exc}") from exc

    background_tasks.add_task(run_pipeline_in_background, str(current_user.id), slug)
    return {"status": "processing", "project_name": slug, "message": f"Project '{slug}' uploaded successfully."}


@app.post("/api/process-git")
async def process_git_repo(payload: GitRepoPayload, background_tasks: BackgroundTasks, current_user=Depends(get_current_user)):
    slug = slugify_project_name(payload.project_name)
    ensure_project_dirs(current_user.id, slug)
    root = project_root(current_user.id, slug)
    if root.exists():
        shutil.rmtree(root)

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", payload.repo_url, str(root)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=result.stderr.strip() or "Failed to clone git repository.")
        write_status(current_user.id, slug, "processing", "clone", f"Repository cloned as '{slug}'.", 5)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to clone repository: {exc}") from exc

    background_tasks.add_task(run_pipeline_in_background, str(current_user.id), slug)
    return {"status": "processing", "project_name": slug, "message": "Repository cloned successfully."}


@app.get("/api/projects")
async def list_user_projects(current_user=Depends(get_current_user)):
    root = user_upload_root(current_user.id)
    if not root.exists():
        return {"projects": []}
    projects = []
    for path in sorted(root.iterdir()):
        if path.name.endswith(".delete_pending"):
            continue
        if path.is_dir():
            status = read_status(current_user.id, path.name)
            if status.get("status") in {"deleted", "delete_pending"}:
                continue
            projects.append({"name": path.name, **status})
    return {"projects": projects}


@app.delete("/api/projects/{project_name}")
async def delete_project(project_name: str, current_user=Depends(get_current_user)):
    slug = slugify_project_name(project_name)
    isolated_name = project_namespace(current_user.id, slug)
    write_status(current_user.id, slug, "delete_pending", "delete", f"Project '{slug}' is being deleted.", 0)
    invalidate_vectorstore_cache(isolated_name)
    cleanup_complete = delete_project_artifacts(current_user.id, slug)
    purge_project_from_chroma(isolated_name)
    if cleanup_complete:
        return {"message": f"Project '{slug}' was deleted.", "cleanup_deferred": False}
    return {
        "message": f"Project '{slug}' was removed from the workspace. Some files are queued for deferred cleanup.",
        "cleanup_deferred": True,
    }


def generate_file_tree(dir_path: Path, base_path: Path):
    tree = []
    for path in sorted(dir_path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if path.name.startswith(".") or path.name == "__pycache__" or path.name == "status.json":
            continue
        rel_path = path.relative_to(base_path)
        node = {"name": path.name, "path": str(rel_path).replace("\\", "/"), "type": "directory" if path.is_dir() else "file"}
        if path.is_dir():
            node["children"] = generate_file_tree(path, base_path)
        tree.append(node)
    return tree


@app.get("/api/project-structure/{project_name}")
async def get_project_structure(project_name: str, current_user=Depends(get_current_user)):
    slug = slugify_project_name(project_name)
    root = project_root(current_user.id, slug)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"tree": generate_file_tree(root, root)}


@app.get("/api/file-content")
async def get_file_content(path: str, project_name: str, current_user=Depends(get_current_user)):
    target = resolve_project_file(current_user.id, project_name, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/project-tree/{project_name}")
async def get_project_tree_endpoint(project_name: str, current_user=Depends(get_current_user)):
    path = tree_cache_path(current_user.id, project_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project file tree map not found.")
    return {"project_name": project_name, "tree": path.read_text(encoding="utf-8")}


@app.get("/api/architecture/{project_name}")
async def get_architecture_diagram(project_name: str, current_user=Depends(get_current_user)):
    path = architecture_path(current_user.id, project_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Architecture diagram not found.")
    return {"project_name": project_name, "mermaid_code": path.read_text(encoding="utf-8")}


@app.get("/api/dependency-graph/{project_name}")
async def get_dependency_graph(project_name: str, current_user=Depends(get_current_user)):
    path = dependency_graph_path(current_user.id, project_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Dependency graph not found.")
    return {"project_name": project_name, "mermaid_code": path.read_text(encoding="utf-8")}


@app.get("/api/chat-history/{project_name}")
async def get_chat_history(project_name: str, current_user=Depends(get_current_user)):
    authenticated_thread_id = f"user_{current_user.id}_{project_name}"
    config = {"configurable": {"thread_id": authenticated_thread_id}}
    state = chat_graph_app.get_state(config)
    messages = []
    if state and state.values and "messages" in state.values:
        for msg in state.values["messages"]:
            role = "user" if msg.type == "human" else "bot"
            if msg.content and "ROUTE_TO_DEBUGGER" not in str(msg.content):
                messages.append({"role": role, "content": msg.content})
    return {"messages": messages}


@app.post("/api/chat")
async def handle_chat_interaction(payload: ChatPayload, current_user=Depends(get_current_user)):
    slug = slugify_project_name(payload.project_name)
    allowed, guardrail_error = screen_user_prompt(payload.query)
    if not allowed:
        raise HTTPException(status_code=400, detail=guardrail_error)

    authenticated_thread_id = f"user_{current_user.id}_{payload.thread_id}"
    project_id = project_namespace(current_user.id, slug)
    config = {"configurable": {"thread_id": authenticated_thread_id}}

    def event_stream():
        try:
            if payload.is_approval:
                if not payload.approval_file_path or payload.approval_new_content is None:
                    yield sse_event("error", content="Approval requires file path and replacement content.")
                else:
                    result = write_project_file_after_approval(project_id, payload.approval_file_path, payload.approval_new_content)
                    yield sse_event("tool_result", tool="write_project_file_after_approval", content=result)
                    yield sse_event("done")
                return

            input_data = {"project_name": project_id, "messages": [HumanMessage(content=payload.query)], "provider_events": []}
            accumulated = ""
            for chunk, _metadata in chat_graph_app.stream(input_data, config=config, stream_mode="messages"):
                if getattr(chunk, "tool_calls", None):
                    for tool_call in chunk.tool_calls:
                        print(f"[tool-call] start {tool_call.get('name', 'tool')} args={tool_call.get('args', {})}", flush=True)
                        yield sse_event("tool_start", tool=tool_call.get("name", "tool"), args=tool_call.get("args", {}))
                    continue

                if chunk.type == "tool":
                    content = str(chunk.content)
                    print(f"[tool-call] result {getattr(chunk, 'name', 'tool')} chars={len(content)}", flush=True)
                    for source in sorted(set(__import__("re").findall(r"File: ([^)\n,]+)", content))):
                        yield sse_event("reference", path=source)
                    if content.startswith("PATCH_PREVIEW_READY"):
                        yield sse_event("approval_required", content="A code patch is ready for review before writing.")
                    else:
                        yield sse_event("tool_result", content=content[:2000])
                    continue

                if chunk.type == "ai" and chunk.content:
                    content = chunk.content
                    if isinstance(content, list):
                        content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
                    content = str(content)
                    if re_search_tool_leak(content):
                        continue
                    accumulated += content
                    yield sse_event("chunk", content=content)

            state = chat_graph_app.get_state(config)
            if state and state.values and state.values.get("provider_events"):
                for event in state.values["provider_events"]:
                    if not isinstance(event, dict):
                        continue
                    provider = event.get("provider", "unknown")
                    elapsed = event.get("elapsed_seconds")
                    if elapsed is not None and provider != "none":
                        yield sse_event("response_meta", elapsed_seconds=elapsed)
            if state and state.values and "messages" in state.values:
                for message in reversed(state.values["messages"]):
                    if getattr(message, "type", None) == "ai" and getattr(message, "content", None):
                        final_content = str(message.content)
                        if "ROUTE_TO_DEBUGGER" not in final_content and final_content not in accumulated:
                            accumulated += final_content
                            yield sse_event("chunk", content=final_content)
                        break

            if accumulated:
                import re
                sources = set(re.findall(r"File: ([^)\n,]+)", accumulated))
                sources.update(re.findall(r"`([^`]+\.(?:py|js|jsx|ts|tsx|java|md|toml|json))`", accumulated))
                for source in sorted(sources):
                    yield sse_event("reference", path=source)
            if state.next:
                yield sse_event("approval_required", content="The debugger wants to use a protected tool. Review before approving.")
            yield sse_event("done")
        except Exception as exc:
            yield sse_event("error", content=f"LangGraph streaming error: {exc}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def re_search_tool_leak(content: str) -> bool:
    import re

    return bool(re.search(r"<function>|</function>|<tool_call>|</tool_call>", content or ""))


@app.post("/api/vision/analyze")
async def analyze_error_screenshot(
    project_name: str = Form(...),
    prompt: str = Form("Analyze this error screenshot and suggest the likely cause."),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    await validate_image_upload(file)
    data = await file.read()
    image_b64 = base64.b64encode(data).decode("utf-8")
    future = _vision_executor.submit(model_router.analyze_image, image_b64, file.content_type or "image/png", prompt)
    try:
        timeout_seconds = max(settings.PROVIDER_TIMEOUT_SECONDS + 10, settings.VISION_ANALYSIS_TIMEOUT_SECONDS)
        result, metadata = future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        result = (
            "Vision analysis timed out before the provider returned a response. "
            "Please paste the visible error text into chat so the debugger can continue with code retrieval."
        )
        metadata = {"provider": "none", "model": "timeout"}
    except Exception as exc:
        result = (
            "Vision analysis failed before a provider response was available. "
            f"Please paste the visible error text into chat. Error: {str(exc)[:200]}"
        )
        metadata = {"provider": "none", "model": "error"}
    return {"analysis": result, "provider": metadata}


if __name__ == "__main__":
    import uvicorn

    def _reload_excludes():
        return [
            "uploads",
            "chroma_db",
            "project_trees",
            "artifacts",
            "data",
            "agentenv",
            "__pycache__",
        ]

    def _reload_dirs():
        source_dirs = ["agents", "grammars", "models", "providers", "tools", "utils"]
        return [str(settings.BASE_DIR / directory) for directory in source_dirs]

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=_reload_dirs(),
        reload_excludes=_reload_excludes(),
    )
