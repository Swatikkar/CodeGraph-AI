from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from config import settings
from prompts import prompt_spec_for
from utils.ai_runtime import AIBudgetExceeded, current_ai_run, estimate_tokens
from utils.observability import log_event
from utils.runtime_metrics import record_runtime_metric


ROLE_TO_SETTING = {
    "supervisor_reasoning": "SUPERVISOR_REASONING_MODELS",
    "debugger_coding": "DEBUGGER_CODING_MODELS",
    "commenter_code_docs": "COMMENTER_CODE_DOCS_MODELS",
    "architecture_design": "ARCHITECTURE_DESIGN_MODELS",
    "retrieval_query": "RETRIEVAL_QUERY_MODELS",
    "vision_error_analysis": "VISION_ERROR_ANALYSIS_MODELS",
    "guardrail": "GUARDRAIL_MODELS",
    "embedding": "EMBEDDING_MODELS",
}


class ProviderError(Exception):
    def __init__(self, provider: str, model: str, message: str):
        super().__init__(message)
        self.provider = provider
        self.model = model


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_routes(raw: str) -> list[ModelRoute]:
    routes: list[ModelRoute] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        provider, model = item.split(":", 1)
        routes.append(ModelRoute(provider=provider.strip().lower(), model=model.strip()))
    return routes


def stringify_messages(messages: Iterable[BaseMessage | tuple | dict | str]) -> str:
    parts = []
    for message in messages:
        if isinstance(message, BaseMessage):
            role = message.type
            content = message.content
        elif isinstance(message, tuple) and len(message) == 2:
            role, content = message
        elif isinstance(message, dict):
            role = message.get("role", "user")
            content = message.get("content", "")
        else:
            role = "user"
            content = str(message)
        parts.append(f"{role.upper()}: {content}")
    return "\n\n".join(parts)


def classify_provider_error(exc: Exception) -> str:
    if isinstance(exc, AIBudgetExceeded):
        return "budget_exceeded"
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if "not configured" in message or "unsupported" in message:
        return "configuration"
    if status_code == 429 or "429" in message or "rate limit" in message or "quota" in message:
        return "rate_limit"
    if status_code in {401, 403} or "api key" in message or "unauthorized" in message or "authentication" in message:
        return "authentication"
    if status_code == 404 or "model_not_found" in message or "not found" in message:
        return "invalid_model"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if status_code and status_code >= 500:
        return "provider_unavailable"
    return "provider_error"


def response_token_usage(response: AIMessage, input_text: str) -> tuple[int, int, str]:
    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
    if input_tokens is None or output_tokens is None:
        token_usage = (getattr(response, "response_metadata", None) or {}).get("token_usage", {})
        input_tokens = input_tokens or token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
        output_tokens = output_tokens or token_usage.get("completion_tokens") or token_usage.get("output_tokens")
    if input_tokens is not None and output_tokens is not None:
        return int(input_tokens), int(output_tokens), "provider"
    return estimate_tokens(input_text), estimate_tokens(str(response.content or "")), "estimated"


class ModelRouter:
    def routes_for_role(self, role: str) -> list[ModelRoute]:
        setting_name = ROLE_TO_SETTING.get(role, "SUPERVISOR_REASONING_MODELS")
        return parse_routes(getattr(settings, setting_name))

    def llm_for_route(self, route: ModelRoute, tools=None, timeout_seconds: float | None = None):
        provider = route.provider
        model = route.model
        timeout = timeout_seconds or settings.PROVIDER_TIMEOUT_SECONDS

        if provider == "groq":
            if not settings.GROQ_API_KEY:
                raise ProviderError(provider, model, "GROQ_API_KEY is not configured.")
            from langchain_groq import ChatGroq

            llm = ChatGroq(
                model=model,
                api_key=settings.GROQ_API_KEY,
                temperature=settings.MODEL_TEMPERATURE,
                timeout=timeout,
                max_retries=settings.PROVIDER_MAX_RETRIES,
                max_tokens=settings.AI_MAX_OUTPUT_TOKENS,
            )
        elif provider == "ollama":
            from langchain_ollama import ChatOllama

            llm = ChatOllama(
                model=model,
                base_url=settings.OLLAMA_BASE_URL,
                temperature=settings.MODEL_TEMPERATURE,
                keep_alive=300,
                num_predict=settings.AI_MAX_OUTPUT_TOKENS,
            )
        elif provider == "gemini":
            if not settings.GEMINI_API_KEY:
                raise ProviderError(provider, model, "GEMINI_API_KEY is not configured.")
            from langchain_google_genai import ChatGoogleGenerativeAI

            llm = ChatGoogleGenerativeAI(
                model=model,
                google_api_key=settings.GEMINI_API_KEY,
                temperature=settings.MODEL_TEMPERATURE,
                timeout=timeout,
                max_retries=settings.PROVIDER_MAX_RETRIES,
                max_output_tokens=settings.AI_MAX_OUTPUT_TOKENS,
            )
        elif provider in {"openrouter", "nvidia"}:
            api_key = settings.OPENROUTER_API_KEY if provider == "openrouter" else settings.NVIDIA_API_KEY
            if not api_key:
                raise ProviderError(provider, model, f"{provider.upper()}_API_KEY is not configured.")
            from langchain_openai import ChatOpenAI

            base_url = "https://openrouter.ai/api/v1" if provider == "openrouter" else "https://integrate.api.nvidia.com/v1"
            llm = ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=settings.MODEL_TEMPERATURE,
                timeout=timeout,
                max_retries=settings.PROVIDER_MAX_RETRIES,
                max_tokens=settings.AI_MAX_OUTPUT_TOKENS,
            )
        else:
            raise ProviderError(provider, model, f"Unsupported chat provider: {provider}")

        if tools:
            try:
                return llm.bind_tools(tools)
            except Exception as exc:
                raise ProviderError(provider, model, f"Provider does not support these tools: {exc}") from exc
        return llm

    def invoke_chat(self, role: str, messages: list[BaseMessage], tools=None) -> tuple[AIMessage, dict]:
        errors = []
        prompt_spec = prompt_spec_for(role)
        input_text = stringify_messages(messages)
        estimated_input_tokens = estimate_tokens(input_text)
        for route in self.routes_for_role(role):
            started_at = time.perf_counter()
            context = current_ai_run()
            try:
                remaining = context.remaining_seconds() if context else settings.PROVIDER_TIMEOUT_SECONDS
                timeout = max(0.1, min(settings.PROVIDER_TIMEOUT_SECONDS, remaining))
                llm = self.llm_for_route(route, tools=tools, timeout_seconds=timeout)
                if context:
                    context.consume_provider_call(estimated_input_tokens)
                log_event(
                    "provider_attempt_started",
                    trace_id=context.trace_id if context else "unscoped",
                    role=role,
                    prompt_id=prompt_spec.prompt_id,
                    prompt_version=prompt_spec.version,
                    provider=route.provider,
                    model=route.model,
                    tools_enabled=bool(tools),
                )
                response = llm.invoke(messages)
                elapsed = round(time.perf_counter() - started_at, 2)
                input_tokens, output_tokens, token_source = response_token_usage(response, input_text)
                record_runtime_metric(
                    kind="provider",
                    component=route.label,
                    status="success",
                    latency_ms=round(elapsed * 1000),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    token_source=token_source,
                    prompt_id=prompt_spec.prompt_id,
                    prompt_version=prompt_spec.version,
                    attributes={"role": role, "fallback_index": len(errors)},
                )
                metadata = {
                    "provider": route.provider,
                    "model": route.model,
                    "elapsed_seconds": elapsed,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "token_source": token_source,
                    "prompt_id": prompt_spec.prompt_id,
                    "prompt_version": prompt_spec.version,
                    "fallback_errors": errors,
                }
                return response, metadata
            except Exception as exc:
                elapsed = round(time.perf_counter() - started_at, 2)
                error_type = classify_provider_error(exc)
                record_runtime_metric(
                    kind="provider",
                    component=route.label,
                    status="skipped" if error_type == "configuration" else "failed",
                    latency_ms=round(elapsed * 1000),
                    input_tokens=estimated_input_tokens if error_type != "configuration" else 0,
                    output_tokens=0,
                    token_source="estimated",
                    prompt_id=prompt_spec.prompt_id,
                    prompt_version=prompt_spec.version,
                    error_type=error_type,
                    attributes={"role": role, "fallback_index": len(errors)},
                )
                errors.append({
                    "provider": route.provider,
                    "model": route.model,
                    "elapsed_seconds": elapsed,
                    "error_type": error_type,
                })
                if error_type == "budget_exceeded":
                    break
        fallback = AIMessage(
            content=(
                "I could not reach any configured model provider for this role. "
                "Please add at least one valid API key/model in backend/.env."
            )
        )
        return fallback, {
            "provider": "none",
            "model": "none",
            "prompt_id": prompt_spec.prompt_id,
            "prompt_version": prompt_spec.version,
            "fallback_errors": errors,
        }

    def stream_chat(self, role: str, messages: list[BaseMessage], tools=None):
        errors = []
        prompt_spec = prompt_spec_for(role)
        input_text = stringify_messages(messages)
        estimated_input_tokens = estimate_tokens(input_text)
        for route in self.routes_for_role(role):
            started_at = time.perf_counter()
            context = current_ai_run()
            try:
                remaining = context.remaining_seconds() if context else settings.PROVIDER_TIMEOUT_SECONDS
                timeout = max(0.1, min(settings.PROVIDER_TIMEOUT_SECONDS, remaining))
                llm = self.llm_for_route(route, tools=tools, timeout_seconds=timeout)
                if context:
                    context.consume_provider_call(estimated_input_tokens)
                yield {"type": "provider_switch", "provider": route.provider, "model": route.model}
                output = ""
                for chunk in llm.stream(messages):
                    content = getattr(chunk, "content", "")
                    if content:
                        output += str(content)
                        yield {"type": "chunk", "content": content}
                elapsed = round(time.perf_counter() - started_at, 2)
                record_runtime_metric(
                    kind="provider",
                    component=route.label,
                    status="success",
                    latency_ms=round(elapsed * 1000),
                    input_tokens=estimated_input_tokens,
                    output_tokens=estimate_tokens(output),
                    token_source="estimated",
                    prompt_id=prompt_spec.prompt_id,
                    prompt_version=prompt_spec.version,
                    attributes={"role": role, "streaming": True, "fallback_index": len(errors)},
                )
                yield {"type": "done"}
                return
            except Exception as exc:
                elapsed = round(time.perf_counter() - started_at, 2)
                error_type = classify_provider_error(exc)
                record_runtime_metric(
                    kind="provider",
                    component=route.label,
                    status="failed",
                    latency_ms=round(elapsed * 1000),
                    input_tokens=estimated_input_tokens,
                    output_tokens=0,
                    token_source="estimated",
                    prompt_id=prompt_spec.prompt_id,
                    prompt_version=prompt_spec.version,
                    error_type=error_type,
                    attributes={"role": role, "streaming": True, "fallback_index": len(errors)},
                )
                errors.append({"provider": route.provider, "model": route.model, "elapsed_seconds": elapsed, "error_type": error_type})
                yield {"type": "provider_switch", "provider": route.provider, "model": route.model, "error_type": error_type}
                if error_type == "budget_exceeded":
                    break
        yield {
            "type": "error",
            "content": "All configured providers failed. Check API keys, model names, and quota limits.",
            "errors": errors,
        }

    def invoke_text(self, role: str, system: str, prompt: str) -> tuple[str, dict]:
        response, metadata = self.invoke_chat(role, [SystemMessage(content=system), HumanMessage(content=prompt)])
        return str(response.content or ""), metadata

    def analyze_image(self, image_b64: str, mime_type: str, prompt: str) -> tuple[str, dict]:
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
            ])
        ]
        response, metadata = self.invoke_chat("vision_error_analysis", messages)
        return str(response.content or ""), metadata

    def embeddings(self):
        errors = []
        for route in self.routes_for_role("embedding"):
            try:
                if route.provider == "local" and route.model == "hashing-v1":
                    from providers.local_embeddings import LocalHashingEmbeddings

                    return LocalHashingEmbeddings()
                if route.provider == "gemini":
                    if not settings.GEMINI_API_KEY:
                        raise ProviderError(route.provider, route.model, "GEMINI_API_KEY is not configured.")
                    from langchain_google_genai import GoogleGenerativeAIEmbeddings

                    return GoogleGenerativeAIEmbeddings(model=route.model, google_api_key=settings.GEMINI_API_KEY)
                if route.provider == "huggingface":
                    if not settings.HUGGINGFACE_API_KEY:
                        raise ProviderError(route.provider, route.model, "HUGGINGFACE_API_KEY is not configured.")
                    from langchain_huggingface import HuggingFaceEndpointEmbeddings

                    return HuggingFaceEndpointEmbeddings(
                        model=route.model,
                        huggingfacehub_api_token=settings.HUGGINGFACE_API_KEY,
                    )
                if route.provider == "ollama":
                    from langchain_ollama import OllamaEmbeddings

                    return OllamaEmbeddings(model=route.model, base_url=settings.OLLAMA_BASE_URL)
                errors.append(f"{route.label}: unsupported embedding provider")
            except Exception as exc:
                errors.append(f"{route.label}: {exc}")
        raise ProviderError("embedding", "none", "No embedding provider is configured. " + " | ".join(errors))


model_router = ModelRouter()
