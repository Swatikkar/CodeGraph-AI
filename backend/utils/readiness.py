from config import settings


def critical_config_errors() -> list[str]:
    errors: list[str] = []

    if settings.is_production and settings.JWT_SECRET_KEY == "fallback_secret_key":
        errors.append("production_jwt_secret_is_not_configured")
    if settings.STORAGE_MODE.lower() == "supabase" and not settings.DATABASE_URL:
        errors.append("supabase_storage_requires_database_url")

    return errors
