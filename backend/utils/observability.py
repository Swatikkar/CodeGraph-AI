import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import Request


def log_event(event: str, **fields) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    print(json.dumps(payload, default=str, separators=(",", ":")), flush=True)


async def request_observability_middleware(request: Request, call_next):
    request_id = uuid.uuid4().hex
    started_at = time.perf_counter()
    request.state.request_id = request_id

    try:
        response = await call_next(request)
    except Exception as exc:
        log_event(
            "request_failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            error_type=type(exc).__name__,
        )
        raise

    response.headers["X-Request-ID"] = request_id
    log_event(
        "request_completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
    )
    return response
