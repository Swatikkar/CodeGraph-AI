import re


SECRET_PATTERNS = [
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "[REDACTED_GOOGLE_API_KEY]"),
    (re.compile(r"\bsk-[0-9A-Za-z_-]{20,}\b"), "[REDACTED_OPENAI_STYLE_KEY]"),
    (re.compile(r"\bhf_[0-9A-Za-z]{20,}\b"), "[REDACTED_HUGGINGFACE_KEY]"),
    (re.compile(r"\bnvapi-[0-9A-Za-z_-]{20,}\b"), "[REDACTED_NVIDIA_KEY]"),
    (re.compile(r"\bgsk_[0-9A-Za-z_-]{20,}\b"), "[REDACTED_GROQ_KEY]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|jwt[_-]?secret|googlemapsapikey)\b"
            r"(\s*[:=]\s*)"
            r"([\"']?)[^\"'\s,;)}]+(\3)"
        ),
        r"\1\2\3[REDACTED]\4",
    ),
]


def redact_secrets(text: str) -> str:
    redacted = text or ""
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def contains_secret(text: str) -> bool:
    value = text or ""
    return any(pattern.search(value) for pattern, _replacement in SECRET_PATTERNS)
