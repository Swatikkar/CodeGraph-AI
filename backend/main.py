import ast
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
import json
import os
import shutil
import subprocess
import threading
import time

APP_IMPORT_STARTED_AT = time.perf_counter()

from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
from graph import codegraph_app
from graph_chat import chat_graph_app, close_chat_storage
from providers import model_router
from tools.file_ops import write_project_file_after_approval
from tools.ingester import purge_project_from_chroma
from tools.retriever import invalidate_vectorstore_cache
from utils.auth import auth_router, get_current_user
from utils.database import SessionLocal, get_db
from utils.guardrails import screen_user_prompt, validate_image_upload, validate_zip_upload
from utils.ingestion_security import (
    IngestionValidationError,
    safe_extract_zip,
    validate_git_repo_url,
    validate_repository_tree,
)
from utils.observability import log_event, request_observability_middleware
from utils.project_persistence import (
    delete_project_from_db,
    ensure_retrieval_cache,
    list_user_projects_from_db,
    materialize_project_from_db,
    persist_chat_message,
    persist_project_snapshot,
    read_chat_messages,
    require_project,
    reserve_project_ingestion,
    storage_enabled,
)
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
from utils.readiness import critical_config_errors


settings.configure_langsmith()
settings.create_required_directories()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log_event(
        "application_started",
        environment=settings.ENVIRONMENT,
        import_seconds=round(time.perf_counter() - APP_IMPORT_STARTED_AT, 3),
    )
    try:
        yield
    finally:
        close_chat_storage()
        log_event("application_stopped")


app = FastAPI(title=settings.APP_NAME, version="2.0.0", lifespan=lifespan)
app.middleware("http")(request_observability_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)

_vision_executor = ThreadPoolExecutor(max_workers=2)
_active_ingestions: set[tuple[str, str]] = set()
_active_ingestions_lock = threading.Lock()


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


def reserve_local_ingestion_slot(user_id: int | str, project_name: str) -> bool:
    key = (str(user_id), slugify_project_name(project_name))
    with _active_ingestions_lock:
        if key in _active_ingestions:
            return False
        _active_ingestions.add(key)
        return True


def release_local_ingestion_slot(user_id: int | str, project_name: str) -> None:
    key = (str(user_id), slugify_project_name(project_name))
    with _active_ingestions_lock:
        _active_ingestions.discard(key)


def public_project_name_for_chat(db: Session, user_id: int | str, slug: str) -> str:
    if not storage_enabled():
        return slug
    try:
        project = require_project(db, user_id, slug)
        return project.display_name or slug
    except Exception:
        return slug


def sanitize_user_facing_text(text: str, user_id: int | str, slug: str, public_name: str | None = None) -> str:
    if not text:
        return text
    import re

    sanitized = str(text)
    namespace = project_namespace(user_id, slug)
    display_name = public_name or slug
    replacements = {
        f"`user_{namespace}`": f"`{display_name}`",
        f"`user-{namespace}`": f"`{display_name}`",
        f"'user_{namespace}'": f"'{display_name}'",
        f"'user-{namespace}'": f"'{display_name}'",
        f'"user_{namespace}"': f'"{display_name}"',
        f'"user-{namespace}"': f'"{display_name}"',
        f"user_{namespace}": display_name,
        f"user-{namespace}": display_name,
        f"`{namespace}`": f"`{display_name}`",
        f"'{namespace}'": f"'{display_name}'",
        f'"{namespace}"': f'"{display_name}"',
        namespace: display_name,
        "Supervisor Agent": "codebase assistant",
        "Debugger Agent": "debugging assistant",
        "CodeGraph AI project": "project",
    }
    for old, new in replacements.items():
        sanitized = sanitized.replace(old, new)
    escaped_slug = re.escape(slug)
    sanitized = re.sub(rf"\buser[_-]?\d+[_-]{escaped_slug}\b", display_name, sanitized)
    sanitized = re.sub(rf"\b\d+_{escaped_slug}\b", display_name, sanitized)
    return sanitized


def sse_event(event_type: str, **payload) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def normalize_ai_content(raw_content) -> str:
    if isinstance(raw_content, list):
        return "".join(item.get("text", "") for item in raw_content if isinstance(item, dict))

    text = str(raw_content)
    if text.startswith("[{") and "'type': 'text'" in text and "'extras':" in text:
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                return "".join(item.get("text", "") for item in parsed if isinstance(item, dict))
        except (SyntaxError, ValueError):
            return ""
    return text


def run_pipeline_in_background(user_id: str, project_name: str):
    try:
        state = {
            "user_id": str(user_id),
            "project_name": project_name,
            "project_path": str(project_root(user_id, project_name)),
            "unprocessed_files": [],
            "processed_files": [],
            "comment_report": [],
            "scanned_files": 0,
            "errors": [],
        }
        recursion_limit = max(50, settings.MAX_PROJECT_FILES + 10)
        codegraph_app.invoke(state, config={"recursion_limit": recursion_limit})
    except Exception as exc:
        write_status(user_id, project_name, "error", "pipeline", "Background analysis failed.", 0, str(exc))
    finally:
        try:
            persist_project_snapshot(user_id, project_name)
        except Exception as exc:
            print(f"[persistence] warning: failed to persist project snapshot: {exc}", flush=True)
        release_local_ingestion_slot(user_id, project_name)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
        "storage_mode": "supabase" if settings.use_supabase_storage else "local",
        "supabase_storage_enabled": settings.use_supabase_storage,
        "database_configured": bool(settings.DATABASE_URL),
    }


@app.get("/api/ready")
async def readiness(db: Session = Depends(get_db)):
    config_errors = critical_config_errors()
    database_ready = False
    try:
        db.execute(text("SELECT 1"))
        database_ready = True
    except Exception as exc:
        log_event("readiness_database_failed", error_type=type(exc).__name__)

    if config_errors or not database_ready:
        log_event(
            "readiness_failed",
            config_error_codes=config_errors,
            database_ready=database_ready,
        )
        raise HTTPException(status_code=503, detail="Service dependencies are not ready.")

    return {"status": "ready", "database": "ready"}


@app.post("/api/process-zip")
async def process_zip_upload(
    background_tasks: BackgroundTasks,
    project_name: str = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await validate_zip_upload(file)
    slug = slugify_project_name(project_name)
    if not reserve_local_ingestion_slot(current_user.id, slug):
        raise HTTPException(status_code=409, detail="This project is already being ingested.")
    if storage_enabled():
        try:
            project = reserve_project_ingestion(
                db,
                current_user.id,
                slug,
                project_name.strip(),
                "zip",
                stage="upload",
                message=f"Project '{slug}' upload started.",
            )
            print(
                f"[persistence] project row ready id={project.id} user={current_user.id} slug={slug} source=zip",
                flush=True,
            )
        except Exception as exc:
            release_local_ingestion_slot(current_user.id, slug)
            print(f"[persistence] failed to create project row user={current_user.id} slug={slug}: {exc}", flush=True)
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=500, detail="Failed to create durable project record.") from exc

    try:
        ensure_project_dirs(current_user.id, slug)
        root = project_root(current_user.id, slug)
        zip_path = user_upload_root(current_user.id) / f"{slug}.zip"
        if root.exists():
            shutil.rmtree(root)
        with zip_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        archive_stats = safe_extract_zip(zip_path, root)
        zip_path.unlink(missing_ok=True)
        log_event(
            "zip_extracted",
            user_id=str(current_user.id),
            project_name=slug,
            **archive_stats,
        )
        write_status(current_user.id, slug, "processing", "extract", f"Project '{slug}' uploaded.", 5)
    except IngestionValidationError as exc:
        zip_path.unlink(missing_ok=True)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        write_status(current_user.id, slug, "error", "extract", "ZIP upload was rejected.", 0, str(exc))
        release_local_ingestion_slot(current_user.id, slug)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if "zip_path" in locals():
            zip_path.unlink(missing_ok=True)
        if "root" in locals() and root.exists():
            shutil.rmtree(root, ignore_errors=True)
        write_status(current_user.id, slug, "error", "extract", "ZIP upload failed.", 0, type(exc).__name__)
        log_event("zip_extraction_failed", user_id=str(current_user.id), project_name=slug, error_type=type(exc).__name__)
        release_local_ingestion_slot(current_user.id, slug)
        raise HTTPException(status_code=500, detail="Failed to handle ZIP upload.") from exc

    background_tasks.add_task(run_pipeline_in_background, str(current_user.id), slug)
    return {"status": "processing", "project_name": slug, "message": f"Project '{slug}' uploaded successfully."}


@app.post("/api/process-git")
async def process_git_repo(
    payload: GitRepoPayload,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    slug = slugify_project_name(payload.project_name)
    try:
        repository_url = validate_git_repo_url(payload.repo_url)
    except IngestionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not reserve_local_ingestion_slot(current_user.id, slug):
        raise HTTPException(status_code=409, detail="This project is already being ingested.")
    if storage_enabled():
        try:
            project = reserve_project_ingestion(
                db,
                current_user.id,
                slug,
                payload.project_name.strip(),
                "git",
                repository_url,
                stage="clone",
                message=f"Repository clone started as '{slug}'.",
            )
            print(
                f"[persistence] project row ready id={project.id} user={current_user.id} slug={slug} source=git",
                flush=True,
            )
        except Exception as exc:
            release_local_ingestion_slot(current_user.id, slug)
            print(f"[persistence] failed to create project row user={current_user.id} slug={slug}: {exc}", flush=True)
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=500, detail="Failed to create durable project record.") from exc

    try:
        ensure_project_dirs(current_user.id, slug)
        root = project_root(current_user.id, slug)
        if root.exists():
            shutil.rmtree(root)
        clone_environment = os.environ.copy()
        clone_environment["GIT_TERMINAL_PROMPT"] = "0"
        clone_environment["GIT_CONFIG_NOSYSTEM"] = "1"
        result = subprocess.run(
            [
                "git", "-c", "protocol.file.allow=never", "clone",
                "--depth", "1", "--single-branch", "--no-tags",
                repository_url, str(root),
            ],
            capture_output=True,
            text=True,
            timeout=settings.GIT_CLONE_TIMEOUT_SECONDS,
            env=clone_environment,
        )
        if result.returncode != 0:
            log_event(
                "git_clone_failed",
                user_id=str(current_user.id),
                project_name=slug,
                return_code=result.returncode,
                git_error=(result.stderr.strip() or "unknown")[-500:],
            )
            raise IngestionValidationError("Repository could not be cloned. Confirm that it is public and the URL is correct.")
        repository_stats = validate_repository_tree(root)
        shutil.rmtree(root / ".git", ignore_errors=True)
        log_event(
            "git_clone_completed",
            user_id=str(current_user.id),
            project_name=slug,
            **repository_stats,
        )
        write_status(current_user.id, slug, "processing", "clone", f"Repository cloned as '{slug}'.", 5)
    except subprocess.TimeoutExpired as exc:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        write_status(current_user.id, slug, "error", "clone", "Repository clone timed out.", 0, "clone_timeout")
        release_local_ingestion_slot(current_user.id, slug)
        raise HTTPException(status_code=504, detail="Repository clone timed out.") from exc
    except IngestionValidationError as exc:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        write_status(current_user.id, slug, "error", "clone", "Repository clone was rejected.", 0, str(exc))
        release_local_ingestion_slot(current_user.id, slug)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if "root" in locals() and root.exists():
            shutil.rmtree(root, ignore_errors=True)
        write_status(current_user.id, slug, "error", "clone", "Repository clone failed.", 0, type(exc).__name__)
        log_event("git_clone_exception", user_id=str(current_user.id), project_name=slug, error_type=type(exc).__name__)
        release_local_ingestion_slot(current_user.id, slug)
        raise HTTPException(status_code=500, detail="Failed to clone repository.") from exc

    background_tasks.add_task(run_pipeline_in_background, str(current_user.id), slug)
    return {"status": "processing", "project_name": slug, "message": "Repository cloned successfully."}


@app.get("/api/projects")
async def list_user_projects(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    if storage_enabled():
        return {"projects": list_user_projects_from_db(db, current_user.id)}

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
async def delete_project(project_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    slug = slugify_project_name(project_name)
    if storage_enabled():
        require_project(db, current_user.id, slug)
    isolated_name = project_namespace(current_user.id, slug)
    write_status(current_user.id, slug, "delete_pending", "delete", f"Project '{slug}' is being deleted.", 0)
    invalidate_vectorstore_cache(isolated_name)
    cleanup_complete = delete_project_artifacts(current_user.id, slug)
    purge_project_from_chroma(isolated_name)
    if storage_enabled():
        delete_project_from_db(db, current_user.id, slug)
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
async def get_project_structure(project_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    slug = slugify_project_name(project_name)
    if storage_enabled():
        materialize_project_from_db(db, current_user.id, slug)
    root = project_root(current_user.id, slug)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"tree": generate_file_tree(root, root)}


@app.get("/api/file-content")
async def get_file_content(path: str, project_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    slug = slugify_project_name(project_name)
    if storage_enabled():
        materialize_project_from_db(db, current_user.id, slug)
    target = resolve_project_file(current_user.id, slug, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/project-tree/{project_name}")
async def get_project_tree_endpoint(project_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    slug = slugify_project_name(project_name)
    if storage_enabled():
        materialize_project_from_db(db, current_user.id, slug)
    path = tree_cache_path(current_user.id, slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project file tree map not found.")
    return {"project_name": project_name, "tree": path.read_text(encoding="utf-8")}


@app.get("/api/architecture/{project_name}")
async def get_architecture_diagram(project_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    slug = slugify_project_name(project_name)
    if storage_enabled():
        materialize_project_from_db(db, current_user.id, slug)
    path = architecture_path(current_user.id, slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Architecture diagram not found.")
    return {"project_name": project_name, "mermaid_code": path.read_text(encoding="utf-8")}


@app.get("/api/dependency-graph/{project_name}")
async def get_dependency_graph(project_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    slug = slugify_project_name(project_name)
    if storage_enabled():
        materialize_project_from_db(db, current_user.id, slug)
    path = dependency_graph_path(current_user.id, slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Dependency graph not found.")
    return {"project_name": project_name, "mermaid_code": path.read_text(encoding="utf-8")}


@app.get("/api/chat-history/{project_name}")
async def get_chat_history(project_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    slug = slugify_project_name(project_name)
    if storage_enabled():
        return {"messages": read_chat_messages(db, current_user.id, slug)}

    authenticated_thread_id = f"user_{current_user.id}_{slug}"
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
async def handle_chat_interaction(payload: ChatPayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    slug = slugify_project_name(payload.project_name)
    if storage_enabled():
        materialize_project_from_db(db, current_user.id, slug)
        ensure_retrieval_cache(db, current_user.id, slug)
    public_project_name = public_project_name_for_chat(db, current_user.id, slug)
    allowed, guardrail_error = screen_user_prompt(payload.query)
    if not allowed:
        raise HTTPException(status_code=400, detail=guardrail_error)

    authenticated_thread_id = f"user_{current_user.id}_{payload.thread_id}"
    project_id = project_namespace(current_user.id, slug)
    config = {"configurable": {"thread_id": authenticated_thread_id}}
    if storage_enabled() and not payload.is_approval:
        persist_chat_message(db, current_user.id, slug, "user", payload.query)

    def event_stream():
        try:
            if payload.is_approval:
                if not payload.approval_file_path or payload.approval_new_content is None:
                    yield sse_event("error", content="Approval requires file path and replacement content.")
                else:
                    result = write_project_file_after_approval(project_id, payload.approval_file_path, payload.approval_new_content)
                    persist_project_snapshot(current_user.id, slug)
                    yield sse_event("tool_result", tool="write_project_file_after_approval", content=result)
                    yield sse_event("done")
                return

            input_data = {
                "project_name": project_id,
                "public_project_name": public_project_name,
                "messages": [HumanMessage(content=payload.query)],
                "provider_events": [],
            }
            accumulated = ""
            for chunk, _metadata in chat_graph_app.stream(input_data, config=config, stream_mode="messages"):
                if getattr(chunk, "tool_calls", None):
                    graph_node = (_metadata or {}).get("langgraph_node", "")
                    tool_owner = "Debugger" if "debugger" in graph_node else "Supervisor"
                    for tool_call in chunk.tool_calls:
                        tool_name = tool_call.get("name", "tool")
                        tool_args = tool_call.get("args", {})
                        if tool_name == "retrieve_code_context":
                            print(f"[agent-tools] {tool_owner} called retrieve_code_context", flush=True)
                        elif tool_name == "read_project_file":
                            print(f"[agent-tools] {tool_owner} called read_project_file", flush=True)
                        elif tool_name == "propose_patch":
                            print(f"[agent-tools] {tool_owner} prepared patch proposal", flush=True)
                            yield sse_event(
                                "patch_preview",
                                file_path=tool_args.get("file_path", ""),
                                new_content=tool_args.get("new_content", ""),
                                summary="Review the proposed file replacement before approving the write.",
                            )
                        else:
                            print(f"[tool-call] start {tool_name} args={tool_args}", flush=True)
                        yield sse_event("tool_start", tool=tool_name, args=tool_args)
                    continue

                if chunk.type == "tool":
                    content = sanitize_user_facing_text(str(chunk.content), current_user.id, slug, public_project_name)
                    print(f"[tool-call] result {getattr(chunk, 'name', 'tool')} chars={len(content)}", flush=True)
                    for source in sorted(set(__import__("re").findall(r"File: ([^)\n,]+)", content))):
                        yield sse_event("reference", path=source)
                    if content.startswith("PATCH_PREVIEW_READY"):
                        yield sse_event("approval_required", content="A code patch is ready for review before writing.")
                    else:
                        yield sse_event("tool_result", content=content[:2000])
                    continue

                if chunk.type == "ai" and chunk.content:
                    content = sanitize_user_facing_text(normalize_ai_content(chunk.content), current_user.id, slug, public_project_name)
                    if "ROUTE_TO_DEBUGGER" in content:
                        continue
                    if re_search_tool_leak(content):
                        continue
                    accumulated += content
                    yield sse_event("chunk", content=content)

            state = chat_graph_app.get_state(config)
            if storage_enabled() and accumulated.strip():
                db_session = SessionLocal()
                try:
                    persist_chat_message(db_session, current_user.id, slug, "bot", accumulated)
                finally:
                    db_session.close()
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
                        final_content = sanitize_user_facing_text(
                            normalize_ai_content(message.content),
                            current_user.id,
                            slug,
                            public_project_name,
                        )
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
    slug = slugify_project_name(project_name)
    print(f"[vision] Vision analysis requested for screenshot in project '{slug}'", flush=True)
    data = await file.read()
    image_b64 = base64.b64encode(data).decode("utf-8")
    future = _vision_executor.submit(model_router.analyze_image, image_b64, file.content_type or "image/png", prompt)
    try:
        timeout_seconds = max(5.0, settings.VISION_ANALYSIS_TIMEOUT_SECONDS)
        if settings.is_production:
            timeout_seconds = min(timeout_seconds, 35.0)
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
