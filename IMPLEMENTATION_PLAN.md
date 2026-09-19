# CodeGraph AI phased production implementation

This plan prioritizes correctness and availability before adding architectural features. Each phase is completed only after its focused tests, local smoke path, metrics, and rollback notes pass. Completed phases may then be committed and pushed for user acceptance testing.

## Free-tier architecture rule

The implementation must work without paid Redis, MongoDB, a paid vector database, or a paid background worker.

- Postgres/Supabase is the durable source of truth.
- Supabase Storage holds immutable ZIP/source snapshots where configured.
- A Postgres-backed job state machine provides recovery. On the free deployment, an in-process worker claims durable jobs and can resume stale work after restart. The same worker interface can later run as a separate service without changing API or job semantics.
- pgvector plus PostgreSQL full-text search replaces ephemeral production Chroma.
- Redis/Valkey remains an optional scaling adapter for notifications, locks, rate limits, and cache—not authoritative data.
- MongoDB is not added because the current data and transaction boundaries are relational.
- MCP is deferred until the internal project/retrieval services and authorization model are stable.

## Phase 0 — Reproducible baseline and current outage

Status: local implementation and phase gate completed on 2026-09-19; deployment verification pending.

Goal: make failures observable and local verification repeatable before changing product logic.

Implementation:

1. Document a working Python runtime/bootstrap and pin backend dependencies.
2. Separate liveness from readiness; readiness checks database/schema and required configuration safely.
3. Add startup timing and structured request/error identifiers.
4. Add a minimal backend test harness and isolated test configuration.
5. Add a repeatable baseline benchmark command and metrics ledger.
6. Fix current frontend lint failure and record bundle baseline.
7. Diagnose the production startup failure using observable health/readiness behavior; production logs remain required if startup never reaches the app.

Gate:

- Backend imports and starts from documented commands.
- Unit/test harness passes without real provider keys.
- `/api/health` and `/api/ready` have deterministic tests.
- Frontend lint and build pass.
- Baseline metrics are recorded.

## Phase 1 — Safe, deterministic ingestion inputs

Goal: make ingestion read-only, bounded, tenant-safe, and failure-correct.

Implementation:

1. Remove LLM source mutation from the ingestion critical path.
2. Enforce exact ignored filenames, secret-file patterns, global file/byte/depth limits, and skip statistics.
3. Harden ZIP manifest validation and streamed extraction.
4. Validate Git scheme/host/IP/credentials; enforce clone and repository limits.
5. Build project trees only from the validated manifest and never follow symlinks.
6. Make duplicate submissions idempotent and prevent destructive same-slug races.
7. Guarantee failed clone/extraction terminal states.
8. Rebuild vector indexes per immutable revision to eliminate stale/duplicate vectors.

Gate:

- Security/unit corpus passes for ZIP, Git, paths, dotfiles, symlinks, and limits.
- Submitted source hashes are identical before and after ingestion.
- Failed setup never remains `processing`.
- Re-ingestion cannot retrieve deleted source.

## Phase 2 — Durable jobs and restart recovery

Goal: no project remains permanently stuck after a process restart.

Implementation:

1. Add Alembic and versioned schema migrations.
2. Add source revisions, ingestion jobs, job attempts, and job events.
3. Persist original source before enqueueing work.
4. Implement Postgres job claiming, leases, heartbeats, retries, cancellation, and stale-job recovery.
5. Make scan, parse, diagram, chunk, embed, and publish stages idempotent.
6. Expose job-specific status/retry/cancel endpoints and UI.

Free-tier deployment:

- The web process hosts a small worker loop, but durable Postgres state makes interruption recoverable.
- A future paid deployment starts the same worker entrypoint as a separate Render worker; API code does not change.

Gate:

- Kill/restart tests at each stage recover to success or a terminal failed state.
- Duplicate workers cannot process one job concurrently.
- Retry limits and safe error codes are visible in UI.

## Phase 3 — Durable hybrid retrieval

Goal: retrieval survives deployment and returns evidence that can be measured.

Implementation:

1. Store versioned embeddings in Supabase pgvector.
2. Add PostgreSQL full-text/symbol/path search.
3. Store language, symbol, line range, parent-child, revision, and content-hash metadata.
4. Combine exact symbol, FTS, and vector candidates; rerank a bounded set.
5. Return structured citations instead of parsing filenames from generated prose.
6. Cache embeddings by model and content hash.

Gate:

- Restart/redeploy does not require re-embedding unchanged content.
- Cross-user and cross-revision isolation tests pass.
- Retrieval benchmark records Recall@k/MRR and latency.

## Phase 4 — Provider resilience and cost control

Goal: prevent quota failures from turning into multi-minute hangs.

Implementation:

1. Classify provider errors and skip invalid/unconfigured routes immediately.
2. Respect `Retry-After`; add bounded jittered retry for transient errors.
3. Add provider circuits/cooldowns and per-job request/token budgets.
4. Implement real runtime fallback for embeddings.
5. Batch provider work and keep deterministic fallbacks for optional artifacts.
6. Record safe provider latency/error metrics without leaking prompts or keys.

Gate:

- Timeout, 429, auth failure, and all-provider-down tests pass.
- Maximum failure latency is bounded and measured.
- Ingestion can finish without optional commenter/architecture LLM calls.

## Phase 5 — Correct agent, patch, and streaming behavior

Goal: make every chat turn fresh, bounded, auditable, and safe.

Implementation:

1. Remove demo-specific patch logic.
2. Track tool budgets per request rather than across the full conversation.
3. Validate project-scoped tool arguments with server-owned context and Pydantic schemas.
4. Persist patch proposals with project/revision/file hashes; approve by proposal ID.
5. Validate syntax/tests before publishing an approved patch.
6. Use a durable production checkpointer/history strategy.
7. Add SSE request/event IDs, heartbeats, cancellation, disconnect handling, and maximum duration.

Gate:

- Repeated questions retrieve fresh context.
- Tool loops terminate within budgets.
- Stale/tampered patch approvals are rejected.
- SSE disconnect/cancellation tests pass.

## Phase 6 — Authentication, quotas, and operational security

Goal: defend the public free-tier service from abuse and unsafe defaults.

Implementation:

1. Fail production startup on fallback secrets or invalid critical configuration.
2. Add login/signup/change-password rate limits and uniform auth errors.
3. Add session/token revocation or versioning and refresh-token rotation if custom auth remains.
4. Add per-user project, storage, upload, job, chat, and provider-cost quotas.
5. Add security headers, safe errors, retention/deletion behavior, and audit events.
6. Add dependency/security scanning to CI.

Gate:

- Auth abuse and quota tests pass.
- Password/session revocation is verified.
- No raw provider/database/internal error is returned to the UI.

## Phase 7 — Production UX, CI, observability, and MCP

Goal: make operation and demonstrations reliable and interview-defensible.

Implementation:

1. Add CI for lint, unit, integration, migration, build, and smoke tests.
2. Add structured job/request logs and an operational dashboard/report from stored events.
3. Add job progress, retry, cancel, failure stage, and recovery UX.
4. Lazy-load Mermaid and cancel stale frontend requests.
5. Add a deployment smoke test from the public URL.
6. Add read-only MCP tools for project tree, symbol search, retrieval, and patch proposals only after authorization tests pass.

Gate:

- Public recruiter smoke test passes twice, including after a backend restart.
- CI blocks a deliberately broken migration/security test.
- MCP cannot cross project/user boundaries or write without the existing approval service.

## Metrics and resume evidence

Every phase appends reproducible measurements to `METRICS_LEDGER.md` with the commit, fixture, environment, sample count, and command. Target metrics include:

- backend import/startup and readiness latency;
- ingestion wall time, files/second, chunks/second, LLM calls, provider wait, and retry count;
- restart recovery time and stuck-job count;
- unchanged embedding cache-hit rate and avoided embedding calls;
- retrieval p50/p95 latency, Recall@k, MRR, and citation accuracy;
- chat time-to-first-event, total latency, tool-call count, and error rate;
- frontend bundle size and build time;
- test count/pass rate and deployment smoke success rate.

Only controlled benchmark results will be used for resume claims. Estimated improvements will be labeled as estimates, never presented as measured results.
