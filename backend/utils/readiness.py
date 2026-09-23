import json

from config import settings


def critical_config_errors() -> list[str]:
    errors: list[str] = []

    if settings.is_production and settings.JWT_SECRET_KEY == "fallback_secret_key":
        errors.append("production_jwt_secret_is_not_configured")
    if settings.STORAGE_MODE.lower() == "supabase" and not settings.DATABASE_URL:
        errors.append("supabase_storage_requires_database_url")
    if any(value <= 0 for value in (
        settings.AI_MAX_PROVIDER_CALLS,
        settings.AI_MAX_TOTAL_INPUT_TOKENS,
        settings.AI_MAX_OUTPUT_TOKENS,
        settings.AI_MAX_REQUEST_SECONDS,
        settings.AGENT_MAX_TOOL_CALLS,
        settings.AGENT_MAX_DUPLICATE_TOOL_CALLS,
        settings.AI_METRICS_RETENTION_DAYS,
    )):
        errors.append("ai_runtime_limits_must_be_positive")
    try:
        pricing = json.loads(settings.AI_PROVIDER_PRICING_JSON or "{}")
        if not isinstance(pricing, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError, TypeError):
        errors.append("ai_provider_pricing_must_be_a_json_object")

    return errors
