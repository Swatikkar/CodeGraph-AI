# CodeGraph AI metrics ledger

Metrics in this file must be reproducible. Each entry records the environment, fixture, sample count, method, and commit/worktree state. Measurements from a developer laptop must not be described as production measurements.

## Baseline — 2026-09-19

Worktree base commit: `2bfc62d` with pre-existing uncommitted backend changes.

### Verification coverage

| Check | Baseline result |
| --- | --- |
| Automated backend tests discovered | 0 |
| Backend syntax compilation | Passed |
| Full backend import through recovered Python runtime | Passed |
| Frontend lint | Failed: 1 error |
| Frontend production build | Passed |
| Public frontend | Loaded |
| Public backend health | Still on Render Application Loading after more than 90 seconds |

### Local backend health latency

Environment: Windows local machine, recovered Python 3.12.14 runtime, Uvicorn on `127.0.0.1:8010`, local configuration.

Method: 10 sequential HTTP GET requests to `/api/health` after application startup.

| Metric | Baseline |
| --- | ---: |
| First request | 123.26 ms |
| Minimum | 1.24 ms |
| Median approximation (sample 5/10) | 1.61 ms |
| Maximum / p95 approximation (10 samples) | 123.26 ms |
| Mean | 13.99 ms |

The sample is intentionally small and is only a Phase 0 local baseline. It is not a production latency claim.

### Backend import/startup baseline

Environment: the same recovered local Python runtime. Method: three fresh processes executing `import main`.

| Metric | Baseline |
| --- | ---: |
| Samples | 2.673 s, 2.426 s, 2.366 s |
| Mean | 2.490 s |
| Minimum | 2.366 s |
| Maximum | 2.673 s |

### Frontend build

Environment: Node 24.10.0, Vite 8.0.16, existing lock file and installed dependencies.

| Metric | Baseline |
| --- | ---: |
| Build result | Passed |
| Build time reported by Vite | 14.41 s |
| Main `index` JavaScript chunk | 528.95 kB raw / 165.31 kB gzip |
| Largest reported lazy/vendor chunk | 593.66 kB raw / 137.74 kB gzip |
| Vite large-chunk warning | Yes |

### Baseline limitations

- No stable benchmark repository/fixture is committed yet.
- No provider calls are included because that would mix quota/network variation into the Phase 0 baseline.
- Production backend logs were not available, so the public startup failure is observed behavior without an assigned root cause.
- Ingestion, recovery, retrieval-quality, and chat metrics will be added only after deterministic fixtures and instrumentation exist.

## Phase 0 verification — 2026-09-19

### Implemented

- Pinned every direct backend dependency and verified the complete dependency graph resolves from PyPI in dry-run mode.
- Added SQLAlchemy connection pre-ping, bounded Postgres connection/pool waits, and connection recycling.
- Added a separate database-backed readiness endpoint.
- Added JSON request/startup diagnostics and an `X-Request-ID` response header.
- Added graceful LangGraph SQLite checkpointer shutdown.
- Added four isolated backend tests.
- Fixed the frontend lint error caused by synchronous state updates in the image-preview effect.

### Phase gate results

| Check | Result |
| --- | --- |
| Backend syntax compilation | Passed |
| Backend tests | 4/4 passed |
| Dependency resolution | Passed |
| Local server startup | Passed; logged 2.133 s import/startup measurement |
| Liveness | HTTP 200 with request ID |
| Readiness | HTTP 200 with database query |
| Signup -> login -> authenticated `/me` smoke | Passed |
| Frontend lint | Passed, improved from 1 error to 0 |
| Frontend production build | Passed |

### Local readiness latency after Phase 0

Method: 10 sequential external HTTP requests to `/api/ready` on the isolated local smoke server.

| Metric | Result |
| --- | ---: |
| Minimum | 2.74 ms |
| Median approximation (sample 5/10) | 3.07 ms |
| Maximum / p95 approximation (10 samples) | 6.45 ms |

This endpoint performs a database query, so it is intentionally more expensive than liveness. The sample proves the mechanism locally; it is not a production SLA.

### Import timing after Phase 0

Three fresh `import main` processes measured 2.455 s, 2.526 s, and 2.443 s (mean 2.475 s). This is effectively unchanged from the 2.490 s baseline (about 0.6% lower) and should be described as no material startup optimization. Phase 0 added observability and reproducibility; it did not claim to solve Render cold start.

### Frontend build after Phase 0

The warm build completed in 12.36 s and the main chunk changed from 528.95 kB to 528.92 kB. The timing difference is not attributed to the lint fix because build cache/machine noise was not controlled. Large-chunk optimization remains a later phase.
