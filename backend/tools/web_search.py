from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from langchain_core.tools import tool
from langchain_community.tools import DuckDuckGoSearchResults

from config import settings


DEFAULT_TRUSTED_DOMAINS = [
    "react.dev",
    "nextjs.org",
    "vite.dev",
    "developer.mozilla.org",
    "docs.python.org",
    "fastapi.tiangolo.com",
    "python.langchain.com",
    "js.langchain.com",
    "docs.pydantic.dev",
    "docs.sqlalchemy.org",
    "npmjs.com",
    "pypi.org",
    "github.com",
]

OFFICIAL_GITHUB_PREFIXES = (
    "github.com/facebook/react",
    "github.com/vercel/next.js",
    "github.com/vitejs/vite",
    "github.com/fastapi/fastapi",
    "github.com/langchain-ai/langchain",
    "github.com/pydantic/pydantic",
    "github.com/sqlalchemy/sqlalchemy",
)

LOW_TRUST_HINTS = (
    "medium.com",
    "dev.to",
    "hashnode",
    "stackoverflow.com",
    "reddit.com",
    "quora.com",
)

ddg_search = DuckDuckGoSearchResults(output_format="list")


def _configured_domains() -> list[str]:
    raw = getattr(settings, "TRUSTED_SEARCH_DOMAINS", "")
    domains = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return domains or DEFAULT_TRUSTED_DOMAINS


def _max_results() -> int:
    return max(1, min(int(getattr(settings, "WEB_SEARCH_MAX_RESULTS", 3)), 5))


def _timeout_seconds() -> float:
    return max(2.0, min(float(getattr(settings, "WEB_SEARCH_TIMEOUT_SECONDS", 8.0)), 20.0))


def _domain_for_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _is_trusted_url(url: str, allowed_domains: list[str]) -> bool:
    domain = _domain_for_url(url)
    if not domain:
        return False
    if domain == "github.com":
        normalized = urlparse(url).netloc.lower() + urlparse(url).path.lower()
        return any(normalized.startswith(prefix) for prefix in OFFICIAL_GITHUB_PREFIXES)
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in allowed_domains)


def _is_low_trust(url: str) -> bool:
    domain = _domain_for_url(url)
    return any(hint in domain for hint in LOW_TRUST_HINTS)


def _compact_text(value: str, limit: int = 260) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    return clean[:limit] + ("..." if len(clean) > limit else "")


def _site_scoped_query(query: str, domain: str) -> str:
    return f"site:{domain} {query}"


def _normalize_results(results) -> list[dict]:
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return []


@tool
def trusted_web_search(query: str, domains: list[str] | None = None) -> str:
    """
    Search trusted software documentation sources for current library/framework/package information.
    Use this for latest/current/update/version/migration/deprecated/import-change questions.
    Prefer local project retrieval for questions about the user's own code.
    """
    started_at = time.perf_counter()
    allowed_domains = [domain.strip().lower() for domain in (domains or _configured_domains()) if domain.strip()]
    max_results = _max_results()
    timeout_seconds = _timeout_seconds()
    print(f"[trusted-web-search] query={query!r} domains={','.join(allowed_domains[:8])}", flush=True)

    trusted = []
    secondary = []
    errors = []
    seen_urls = set()
    for domain in allowed_domains:
        if len(trusted) >= max_results:
            break
        if time.perf_counter() - started_at > timeout_seconds:
            break
        try:
            raw_results = ddg_search.invoke(_site_scoped_query(query, domain))
        except Exception as exc:
            errors.append(f"{domain}: {exc}")
            continue
        for result in _normalize_results(raw_results):
            url = result.get("link") or result.get("href") or result.get("url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            item = {
                "title": _compact_text(result.get("title") or "Untitled", 120),
                "snippet": _compact_text(result.get("snippet") or result.get("body") or ""),
                "url": url,
                "domain": _domain_for_url(url),
            }
            if _is_trusted_url(url, allowed_domains):
                trusted.append(item)
            elif not _is_low_trust(url):
                secondary.append(item)
            if len(trusted) >= max_results:
                break

    elapsed = round(time.perf_counter() - started_at, 2)
    if elapsed > timeout_seconds:
        print(f"[trusted-web-search] exceeded desired timeout: {elapsed}s > {timeout_seconds}s", flush=True)

    selected = trusted[:max_results]
    if not selected and secondary:
        selected = secondary[:max_results]

    if not selected:
        return (
            f"No trusted official documentation results found for: {query}\n"
            f"Allowed domains searched: {', '.join(allowed_domains)}\n"
            f"Search errors: {' | '.join(errors[:3]) if errors else 'none'}"
        )

    lines = [
        f"Trusted web search results for: {query}",
        f"Elapsed seconds: {elapsed}",
    ]
    for idx, item in enumerate(selected, start=1):
        trust = "trusted" if _is_trusted_url(item["url"], allowed_domains) else "secondary"
        lines.append(
            f"{idx}. [{trust}] {item['title']}\n"
            f"   domain: {item['domain']}\n"
            f"   url: {item['url']}\n"
            f"   snippet: {item['snippet']}"
        )
    return "\n".join(lines)


# Backwards-compatible import name for older code paths.
web_search = trusted_web_search
