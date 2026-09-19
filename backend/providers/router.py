from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from config import settings


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


def _short_error(exc: Exception, limit: int = 500) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:limit] + ("..." if len(message) > limit else "")


def _log_provider(message: str):
    print(f"[model-router] {message}", flush=True)


class ModelRouter:
    def routes_for_role(self, role: str) -> list[ModelRoute]:
        setting_name = ROLE_TO_SETTING.get(role, "SUPERVISOR_REASONING_MODELS")
        return parse_routes(getattr(settings, setting_name))

    def llm_for_route(self, route: ModelRoute, tools=None):
        provider = route.provider
        model = route.model

        if provider == "groq":
            if not settings.GROQ_API_KEY:
                raise ProviderError(provider, model, "GROQ_API_KEY is not configured.")
            from langchain_groq import ChatGroq

            llm = ChatGroq(
                model=model,
                api_key=settings.GROQ_API_KEY,
                temperature=settings.MODEL_TEMPERATURE,
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
                max_retries=settings.PROVIDER_MAX_RETRIES,
            )
        elif provider == "ollama":
            from langchain_ollama import ChatOllama

            llm = ChatOllama(
                model=model,
                base_url=settings.OLLAMA_BASE_URL,
                temperature=settings.MODEL_TEMPERATURE,
                keep_alive=300,
            )
        elif provider == "gemini":
            if not settings.GEMINI_API_KEY:
                raise ProviderError(provider, model, "GEMINI_API_KEY is not configured.")
            from langchain_google_genai import ChatGoogleGenerativeAI

            llm = ChatGoogleGenerativeAI(
                model=model,
                google_api_key=settings.GEMINI_API_KEY,
                temperature=settings.MODEL_TEMPERATURE,
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
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
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
                max_retries=settings.PROVIDER_MAX_RETRIES,
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
        for route in self.routes_for_role(role):
            started_at = time.perf_counter()
            tool_note = " with tools" if tools else ""
            _log_provider(f"{role}: trying {route.label}{tool_note}")
            try:
                llm = self.llm_for_route(route, tools=tools)
                response = llm.invoke(messages)
                elapsed = round(time.perf_counter() - started_at, 2)
                _log_provider(f"Provider selected successfully for {role}: {route.label} in {elapsed}s")
                metadata = {
                    "provider": route.provider,
                    "model": route.model,
                    "elapsed_seconds": elapsed,
                    "fallback_errors": errors,
                }
                return response, metadata
            except Exception as exc:
                elapsed = round(time.perf_counter() - started_at, 2)
                error = _short_error(exc)
                _log_provider(f"Provider failed/unavailable for {role}: {route.label} after {elapsed}s: {error}")
                _log_provider(f"Trying next provider for {role}")
                errors.append({
                    "provider": route.provider,
                    "model": route.model,
                    "elapsed_seconds": elapsed,
                    "error": error,
                })
        fallback = AIMessage(
            content=(
                "I could not reach any configured model provider for this role. "
                "Please add at least one valid API key/model in backend/.env."
            )
        )
        _log_provider(f"{role}: all configured routes failed")
        return fallback, {"provider": "none", "model": "none", "fallback_errors": errors}

    def stream_chat(self, role: str, messages: list[BaseMessage], tools=None):
        errors = []
        for route in self.routes_for_role(role):
            started_at = time.perf_counter()
            _log_provider(f"{role}: streaming {route.label}")
            try:
                llm = self.llm_for_route(route, tools=tools)
                yield {"type": "provider_switch", "provider": route.provider, "model": route.model}
                for chunk in llm.stream(messages):
                    content = getattr(chunk, "content", "")
                    if content:
                        yield {"type": "chunk", "content": content}
                elapsed = round(time.perf_counter() - started_at, 2)
                _log_provider(f"{role}: stream success {route.label} in {elapsed}s")
                yield {"type": "done"}
                return
            except Exception as exc:
                elapsed = round(time.perf_counter() - started_at, 2)
                error = _short_error(exc)
                _log_provider(f"{role}: stream failed {route.label} after {elapsed}s: {error}")
                errors.append({"provider": route.provider, "model": route.model, "elapsed_seconds": elapsed, "error": error})
                yield {"type": "provider_switch", "provider": route.provider, "model": route.model, "error": error}
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

                    _log_provider(f"embedding: selected {route.label}")
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
