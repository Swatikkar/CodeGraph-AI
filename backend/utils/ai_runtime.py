from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from config import settings


class AIBudgetExceeded(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)


@dataclass
class AIRunContext:
    trace_id: str
    user_id: str
    project_slug: str | None
    started_at: float = field(default_factory=time.perf_counter)
    provider_calls: int = 0
    input_tokens: int = 0
    tool_calls: int = 0
    tool_signatures: dict[str, int] = field(default_factory=dict)
    metrics: list[dict] = field(default_factory=list)

    def remaining_seconds(self) -> float:
        return settings.AI_MAX_REQUEST_SECONDS - (time.perf_counter() - self.started_at)

    def consume_provider_call(self, input_tokens: int) -> None:
        if self.remaining_seconds() <= 0:
            raise AIBudgetExceeded("AI request time budget was exhausted.")
        if self.provider_calls >= settings.AI_MAX_PROVIDER_CALLS:
            raise AIBudgetExceeded("AI provider-call budget was exhausted.")
        if self.input_tokens + input_tokens > settings.AI_MAX_TOTAL_INPUT_TOKENS:
            raise AIBudgetExceeded("AI input-token budget was exhausted.")
        self.provider_calls += 1
        self.input_tokens += input_tokens

    def consume_tool_calls(self, tool_calls: list[dict]) -> None:
        if self.remaining_seconds() <= 0:
            raise AIBudgetExceeded("AI request time budget was exhausted.")
        if self.tool_calls + len(tool_calls) > settings.AGENT_MAX_TOOL_CALLS:
            raise AIBudgetExceeded("Agent tool-call budget was exhausted.")
        for tool_call in tool_calls:
            payload = json.dumps(
                {"name": tool_call.get("name"), "args": tool_call.get("args", {})},
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
            signature = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            count = self.tool_signatures.get(signature, 0)
            if count >= settings.AGENT_MAX_DUPLICATE_TOOL_CALLS:
                raise AIBudgetExceeded("Duplicate agent tool call was blocked.")
            self.tool_signatures[signature] = count + 1
        self.tool_calls += len(tool_calls)


_current_ai_run: ContextVar[AIRunContext | None] = ContextVar("current_ai_run", default=None)


@contextmanager
def ai_request_scope(trace_id: str, user_id: int | str, project_slug: str | None):
    context = AIRunContext(trace_id=trace_id, user_id=str(user_id), project_slug=project_slug)
    token = _current_ai_run.set(context)
    try:
        yield context
    finally:
        from utils.runtime_metrics import flush_runtime_metrics

        flush_runtime_metrics(context)
        _current_ai_run.reset(token)


def current_ai_run() -> AIRunContext | None:
    return _current_ai_run.get()
