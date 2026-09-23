"""Read-only public deployment smoke test.

Required: BACKEND_URL and FRONTEND_URL. Optional SMOKE_TOKEN verifies /api/auth/me.
The script never creates users, projects, or provider calls.
"""
import json
import os
import sys
import urllib.error
import urllib.request


def fetch(url: str, token: str | None = None) -> tuple[int, object]:
    headers = {"User-Agent": "CodeGraph-deployment-smoke/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                pass
            return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> int:
    backend = os.environ.get("BACKEND_URL", "").rstrip("/")
    frontend = os.environ.get("FRONTEND_URL", "").rstrip("/")
    token = os.environ.get("SMOKE_TOKEN")
    if not backend or not frontend:
        print("BACKEND_URL and FRONTEND_URL are required.")
        return 2

    checks = [
        ("backend health", f"{backend}/api/health", 200, None),
        ("backend readiness", f"{backend}/api/ready", 200, None),
        ("frontend", frontend, 200, None),
    ]
    if token:
        checks.append(("authenticated identity", f"{backend}/api/auth/me", 200, token))

    failures = []
    for name, url, expected, auth_token in checks:
        status, _body = fetch(url, auth_token)
        print(f"{name}: HTTP {status}")
        if status != expected:
            failures.append(name)
    if failures:
        print("Failed checks: " + ", ".join(failures))
        return 1
    print("Deployment smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
