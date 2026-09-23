from collections import defaultdict, deque
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from config import settings
from utils.observability import log_event


class SlidingWindowLimiter:
    def __init__(self):
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (current - events[0])))
                return False, retry_after
            events.append(current)
            return True, 0


request_limiter = SlidingWindowLimiter()


def _policy(path: str) -> tuple[int, int] | None:
    if path in {"/api/auth/login", "/api/auth/signup", "/api/auth/change-password", "/api/auth/logout-all"}:
        return settings.AUTH_RATE_LIMIT_ATTEMPTS, settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
    if path == "/api/chat":
        return settings.CHAT_RATE_LIMIT_REQUESTS, settings.CHAT_RATE_LIMIT_WINDOW_SECONDS
    if path in {"/api/process-git", "/api/process-zip"}:
        return settings.INGESTION_RATE_LIMIT_REQUESTS, settings.INGESTION_RATE_LIMIT_WINDOW_SECONDS
    return None


async def rate_limit_middleware(request: Request, call_next):
    policy = _policy(request.url.path)
    if policy:
        client_ip = request.client.host if request.client else "unknown"
        limit, window = policy
        allowed, retry_after = request_limiter.allow(f"{client_ip}:{request.url.path}", limit, window)
        if not allowed:
            log_event("request_rate_limited", path=request.url.path, client=client_ip)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Try again later."},
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)
