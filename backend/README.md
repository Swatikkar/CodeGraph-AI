# CodeGraph AI backend

## Supported runtime

- Python 3.12
- Git available on `PATH`

Create a fresh environment from the repository root:

```powershell
cd backend
py -3.12 -m venv agentenv
.\agentenv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If the existing `agentenv` points to a Python installation that no longer exists, remove only that virtual environment and recreate it with the commands above. Do not copy a virtual environment between machines.

## Run locally

Copy `.env.example` to `.env`, provide only the provider keys you intend to test, then run:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Check both endpoints:

- `GET /api/health` proves that the HTTP process is alive.
- `GET /api/ready` verifies critical configuration and a database query. A deployment should not receive normal traffic until readiness returns HTTP 200.

## Tests

The Phase 0 suite is isolated from the developer database and provider keys:

```powershell
python -m unittest discover -s tests -v
```

The tests create temporary SQLite/storage directories and close the LangGraph checkpointer and SQLAlchemy pool before cleanup.

## Structured request diagnostics

Every HTTP response includes `X-Request-ID`. Backend logs emit one-line JSON records with the request ID, method, path, status, and duration. Use the same request ID to correlate a browser error with backend logs without exposing source code, tokens, prompts, or API keys.

## Dependency policy

Direct backend dependencies are pinned in `requirements.txt`. Dependency upgrades are deliberate changes: resolve/install them in a clean Python 3.12 environment, run backend tests, frontend checks, and the smoke path, then record the results in `METRICS_LEDGER.md`.
