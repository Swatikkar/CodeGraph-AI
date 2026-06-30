from pathlib import Path

from fastapi import HTTPException, UploadFile

from config import IGNORE_EXTS, settings


RISKY_PROMPT_MARKERS = [
    "ignore previous instructions",
    "ignore your system prompt",
    "bypass workspace",
    "read /",
    "read c:\\",
    "read ~/.ssh",
    "read ..",
    "exfiltrate",
    "leak secrets",
    "show api key",
    "show .env",
    "show environment variables",
    "print environment variables",
    "dump env",
    "dump secrets",
    "reveal token",
    "reveal credentials",
    "jwt_secret",
    "langsmith_api_key",
]


def screen_user_prompt(text: str) -> tuple[bool, str | None]:
    lowered = (text or "").lower()
    for marker in RISKY_PROMPT_MARKERS:
        if marker in lowered:
            return False, f"Prompt blocked by guardrail: '{marker}' is not allowed."
    return True, None


def assert_supported_code_file(path: Path):
    if path.suffix.lower() in IGNORE_EXTS:
        raise HTTPException(status_code=400, detail="This file type is not available for code analysis.")
    if path.stat().st_size > settings.MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail="File is too large to inspect in the workspace.")


async def validate_zip_upload(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files are supported.")
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > settings.MAX_ZIP_BYTES:
            raise HTTPException(status_code=413, detail="ZIP file exceeds the configured upload limit.")
    await file.seek(0)


async def validate_image_upload(file: UploadFile):
    if file.content_type not in settings.ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only PNG, JPEG, and WebP screenshots are supported.")
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > settings.MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds the configured upload limit.")
    await file.seek(0)
