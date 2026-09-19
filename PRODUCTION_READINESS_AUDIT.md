# CodeGraph AI production-readiness audit

Date: 2026-09-19

This audit is based on the current checkout, a frontend build/lint run, Python syntax compilation, and a read-only check of the public frontend and backend health endpoint. It distinguishes confirmed code defects from architectural risks that still require production logs or failure injection to prove.

## Verification snapshot

- Frontend production build: passed. Vite reported multiple chunks above 500 kB.
- Frontend lint: failed in `frontend/src/pages/Workspace.jsx` because the image-preview effect synchronously sets state.
- Backend syntax compilation: passed with the Codex bundled Python runtime.
- Backend runtime tests: not reproducible from the checked-in `backend/agentenv`; its Python executable points to a missing installation.
- Automated tests: none found in the repository.
- Public frontend: loaded successfully.
- Public backend health: remained on Render's Application Loading page for more than 90 seconds. This confirms current unavailability/very slow startup, but Render logs are required to distinguish a cold-start problem from a startup crash.
- Existing working tree changes were present in `backend/main.py`, `backend/graph_chat.py`, and `backend/agents/supervisor.py`; this audit did not overwrite them.

## Executive conclusion

The supplied review plan is directionally correct, but the most urgent problem is broader than replacing `BackgroundTasks`. Ingestion currently modifies the user's source code, makes one or more LLM calls per discovered file or symbol, writes critical state to an ephemeral filesystem, and only persists a database snapshot after the pipeline finishes or fails. A restart can therefore lose the only ZIP copy and leave a durable project row in `processing` with no recoverable input.

The best production target for this project is:

- Postgres/Supabase: users, projects, ingestion jobs, job events, source-file metadata, chunks, chat history, audit records, and pgvector embeddings.
- Object storage: original ZIP uploads and immutable source snapshots.
- Dedicated worker: claims durable jobs, heartbeats, retries, and resumes idempotent stages.
- Redis/Valkey: optional queue notification, distributed locks, rate limiting, provider circuit state, and short-lived caches. It is not the source of truth.
- MongoDB: do not add it. The current data is relational and already belongs in Postgres; a second durable database would add operational cost without solving a demonstrated problem.
- MCP: add later as a read-only external adapter over stable project/retrieval services. It does not solve ingestion reliability.

## P0: confirmed correctness, security, and data-loss defects

### 1. Ingestion changes the repository before analysis

Evidence:

- `backend/graph.py` loops through `commenter_node` once per discovered file.
- `backend/agents/commenter.py` sends functions/classes or complete files to a model and writes the returned text back to the uploaded/cloned file.
- Embeddings, diagrams, database snapshots, file display, and later patches operate on this modified copy.

Impact:

- The product analyzes AI-modified code rather than the submitted code.
- A malformed model response can break syntax or silently change behavior.
- Ingestion time and provider cost grow with repository size.
- Source content is sent to external providers without a per-project privacy choice.

Fix:

- Make ingestion read-only by default.
- Remove the commenter stage from the critical ingestion path.
- If documentation suggestions remain a feature, generate them as separate artifacts/diffs with explicit user approval; never overwrite the canonical source snapshot.

### 2. Durable recovery is impossible for interrupted ZIP ingestion

Evidence:

- `backend/main.py` deletes the uploaded ZIP immediately after local extraction.
- `persist_project_snapshot` runs only in the background function's `finally` block.
- Render's normal filesystem is ephemeral.

Impact:

- If the service stops before the snapshot finishes, Postgres can retain a `processing` project row while both the ZIP and extracted source disappear.
- There is nothing durable to retry.

Fix:

- Upload the original archive to object storage first.
- In one database transaction create the project, immutable source revision, and queued job.
- A worker downloads the source revision and processes idempotent stages.

### 3. Failed setup paths leave projects stuck in `processing`

Evidence:

- Project rows are created before ZIP extraction and Git clone.
- Clone/extraction exceptions return an HTTP error without reliably updating or deleting the database project row.
- Status synchronization catches database errors and only prints a warning.

Impact:

- An invalid archive, failed clone, schema/connectivity problem, or final status-sync failure can leave the dashboard permanently polling a `processing` row.
- The pipeline may be locally `ready` while the database remains `processing`.

Fix:

- Model project status and job status separately.
- Wrap creation/enqueue in a transaction.
- On pre-processing failure, atomically mark the job/project failed with a stable error code.
- Never swallow a failure to persist the terminal job state; retry it and emit an operational alert.

### 4. Re-ingesting the same project can mix old and new vectors

Evidence:

- ZIP/Git ingestion deletes the source directory for the slug but does not purge its Chroma directory.
- `Chroma.from_documents` writes into the existing persistent directory.

Impact:

- Deleted or changed source can remain retrievable.
- Repeated ingestion can duplicate chunks and return stale answers.

Fix:

- Give every ingestion an immutable `revision_id`.
- Build a new vector index for that revision and atomically mark it active after validation.
- If Chroma is retained, delete/recreate the collection idempotently. Prefer pgvector for this deployment.

### 5. Secret-file filtering is broken for dotfiles

Evidence:

- The ignore set contains `.env` and `.gitignore`, but the scanner compares only `os.path.splitext(filename)[1]`.
- Dotfiles such as `.env` have an empty extension and can be scanned, sent to an LLM, embedded, and persisted.
- Secret redaction covers a small set of token formats and generic key names; it does not guarantee removal of database URLs, cloud credentials, private keys, or arbitrary secrets.

Impact:

- Private credentials can be transmitted to model/embedding providers and stored in Postgres/Chroma.

Fix:

- Add exact ignored filenames and glob patterns (`.env*`, private keys, credential files).
- Run secret scanning before any external call or persistence.
- Record only a safe skipped-file event, never secret content.
- Add explicit consent and retention/deletion policy for private repositories.

### 6. Git ingestion accepts arbitrary locations

Evidence:

- The active endpoint passes `payload.repo_url` directly to `git clone`.
- The stricter GitHub-only helper exists but is unused.

Impact:

- The server can be made to attempt local paths, SSH URLs, or internal HTTP locations.
- Clone stderr is returned directly to the client.

Fix:

- Parse with `urllib.parse`; allow HTTPS and an explicit host allowlist.
- Reject credentials, local/file/SSH schemes, IP literals, localhost/private/link-local destinations, redirects to disallowed hosts, and nonstandard ports unless explicitly supported.
- Enforce clone timeout, shallow/filter clone, post-clone byte/file limits, and safe user-facing errors.

### 7. ZIP extraction is vulnerable to resource exhaustion

Evidence:

- The current extractor checks only absolute/path-traversal targets and then calls `extractall`.
- The configured archive limit is 1 GB, too large for the free web instance.
- There is no extracted-byte, compression-ratio, file-count, depth, path-length, duplicate-path, special-file, or nested-archive limit.

Fix:

- Stream members individually after validating the entire manifest.
- Reject encrypted entries, symlinks/special files, duplicates after normalized/case-folded paths, excessive ratios/depth/count/size, nested archives, and oversized names.
- Use server capacity to set a much smaller compressed limit; do not make the browser decompress/recompress very large archives.

### 8. The scanner does not enforce `MAX_PROJECT_FILES`

Evidence:

- Reaching the limit breaks only the current directory's file loop; `os.walk` continues into subsequent directories.

Impact:

- A repository with many directories can exceed the advertised cap and trigger far more model calls, writes, chunks, and database rows.

Fix:

- Stop traversal globally when the cap is reached.
- Track deterministic skip reasons and expose them in the ingestion report.
- Apply the same limit to database snapshotting and chunk creation, which currently rescan independently.

### 9. Project-tree generation is unbounded and follows directory links

Evidence:

- The API recursively traverses almost every non-hidden directory and does not use the scanner ignore list, maximum depth, maximum node count, or symlink rejection.
- The local checkout contains an ingested repository with `node_modules`; the upload tree contains more than 44,000 files.

Impact:

- Opening a workspace can be slow or exhaust memory/stack.
- A malicious symlink can cause traversal outside the project while building the tree.

Fix:

- Generate the tree from the validated scan manifest, not a second raw filesystem walk.
- Never follow symlinks. Limit depth and nodes and return a truncation summary.

### 10. Agent behavior becomes stale across chat turns

Evidence:

- `has_tool_results`, tool counts, and debugger tool-name checks inspect all messages in the persisted LangGraph thread.
- After tools are used in an earlier turn, later turns can skip fresh retrieval/read operations or terminate after the cumulative tool count reaches the threshold.

Impact:

- The first code question may work while later questions answer from stale history.

Fix:

- Add a server-generated `request_id`/turn boundary to state.
- Count calls and results only for the current run.
- Enforce per-run limits with graph state, wall-clock deadlines, and token/tool budgets.

### 11. The debugger contains demo-specific patch logic

Evidence:

- `backend/agents/debugger.py` hard-codes `calculateDeliveryEta` and one exact JavaScript line.

Impact:

- The system appears general but contains a one-off portfolio demo path.
- Interviewers can correctly challenge it as brittle and non-generalizable.

Fix:

- Delete the hard-coded patch function.
- Use retrieval -> exact file read -> structured patch proposal -> syntax/test validation -> user approval.
- Store a server-side proposal record and require its ID/version/hash on approval.

### 12. The web-search allowlist can be overridden by the model

Evidence:

- The tool accepts a caller-provided `domains` list and treats those values as the allowlist.

Impact:

- A model or prompt injection can turn a supposedly trusted-doc search into an arbitrary-domain search.

Fix:

- Remove `domains` from the public tool schema, or intersect requested domains with a server-owned immutable allowlist.

## P1: production reliability gaps

### Durable ingestion jobs

Add `ingestion_jobs` and `ingestion_job_events` tables. Minimum job fields:

- `id`, `project_id`, `source_revision_id`
- `status`: queued, running, retryable, succeeded, failed, cancelled
- `stage`, `progress`, `attempt_count`, `max_attempts`
- `claimed_by`, `heartbeat_at`, `available_at`
- `started_at`, `finished_at`
- `error_code`, safe `error_message`, private diagnostic reference
- `created_at`, `updated_at`

Use a worker claim transaction (`FOR UPDATE SKIP LOCKED`) or a mature queue. Heartbeat running jobs, recover stale leases, and make every stage idempotent. Redis/Valkey may notify workers and hold locks/circuit state, but Postgres remains authoritative.

### Provider routing

Confirmed current behavior:

- Chat providers are tried sequentially with no circuit breaker or `Retry-After` handling.
- A role can wait through several 20-second timeouts.
- Render's production routes already omit Ollama; the supplied plan's claim that Ollama is tried in production is stale for the current `render.yaml`.
- Embedding fallback is not real runtime fallback: the router returns the first embedding client that can be constructed, while quota/network failure occurs later during `embed_documents`/`embed_query` and is not routed to the next provider.

Implement:

- Per-provider health/circuit state with cool-downs.
- Failure classification: auth/config errors fail fast; 429 respects `Retry-After`; retry transient 5xx/timeouts with bounded jitter; invalid requests do not retry.
- Per-job request/token/cost budgets.
- Batch embeddings and cache them by `(model, content_hash)`.
- Remove per-file LLM commenting from ingestion; deterministic parsing should be the normal path.

### Durable vectors and hybrid retrieval

The database already stores chunks but the `embedding` field is text and is always written as `None`; therefore it cannot rebuild semantic search without paying the embedding provider again.

Recommended design:

- Store vectors in Supabase Postgres with pgvector and index them per model/dimension.
- Add PostgreSQL full-text search for exact symbols/identifiers.
- Attach structured metadata: revision, path, language, symbol name/kind, line range, content hash, parent symbol.
- Retrieve exact symbol/path matches first, combine FTS and vector candidates, then rerank a bounded set.
- Return structured references from the retriever rather than extracting filenames from generated text with regex.

### Database and migrations

- Replace import-time `Base.metadata.create_all` with Alembic migrations run in deployment.
- Add explicit relationships/cascades where they improve domain code, but treat this as maintainability—not the main outage fix.
- Add `pool_pre_ping`, sensible pool limits/timeouts, and deployment-specific settings for the Supabase pooler.
- Add real readiness checks for database connectivity and schema revision. Keep liveness cheap and separate.
- Do not swallow terminal status persistence errors.

### Chat and streaming

- Current SSE framing is appropriate; WebSockets are unnecessary for one-request/one-response chat.
- The graph nodes call non-streaming model invocation, so current SSE chunks may be whole graph/model messages rather than true token streaming.
- Add request IDs, event IDs, heartbeat comments/events, client-disconnect detection, cancellation, a maximum duration, and structured safe errors.
- Server-generate the thread key from authenticated user + project + conversation. Do not trust a client-provided thread ID as the namespace.
- Replace the process-local SQLite LangGraph saver in production with a Postgres checkpointer or one deliberate durable history system; avoid conflicting duplicate histories.

### Auth and abuse controls

- Fail startup in production if `JWT_SECRET_KEY` is the fallback value.
- Add login/signup/change-password rate limits and uniform authentication failures.
- Use short-lived access tokens plus refresh-token rotation/session versioning if custom auth is retained.
- Password changes should revoke or version existing sessions.
- Add user/project storage, file, job, provider-cost, and chat-size quotas.
- Keep bearer-token auth or move fully to hardened cookies. CSRF protection is needed only if authentication is moved to cookies.

## P2: maintainability, performance, and UX

- Pin backend dependencies with hashes/lock output; frontend already has a lock file.
- Add structured JSON logs with `request_id`, `job_id`, `project_id`, stage, attempt, provider/model, duration, counts, and safe error code. Do not log source, secrets, raw tokens, or provider credentials.
- Fix the image-preview lint error and introduce request cancellation on workspace changes/unmount.
- Poll a specific job-status endpoint every few seconds with backoff rather than listing every project once per minute.
- Return HTTP 202 with project/job IDs and idempotency handling for duplicate submissions.
- Provide retry, cancel, delete-failed-project, and failure-stage UX.
- Lazy-load Mermaid/diagram code to reduce the initial frontend bundle.
- Remove unused/unsafe legacy helpers (`tools/extractor.py`, `tools/gitclone.py`) after references are proven absent.
- Replace print statements and broad swallowed exceptions with structured, classified handling.

## Testing gate before calling the app production-grade

### Unit

- Slug/path/namespace isolation and symlink handling
- ZIP traversal, duplicate paths, ratios, extracted bytes, count, depth, special files
- Git URL/host/IP validation
- Exact ignored filenames and secret-file patterns
- Scanner global cap and skip statistics
- Provider error classification, backoff, circuit transitions, and embedding fallback
- Job state-machine transitions and stale lease recovery
- Current-turn agent tool limits

### Integration

- Auth lifecycle and rate limiting
- Project/job transaction and idempotent duplicate submission
- Git/ZIP source revision persistence
- Worker claim/heartbeat/retry/cancel
- pgvector + full-text hybrid retrieval with project/revision isolation
- Structured citations
- Patch proposal binding, stale-version rejection, approval audit, syntax/test validation

### Failure and recovery

- Kill the worker during every ingestion stage, then verify recovery
- Provider timeout/429/auth error/all providers unavailable
- Database disconnect during heartbeat and terminal commit
- Missing/corrupt vector index
- Empty/generated-only/oversized/malicious repositories
- Concurrent re-ingestion and deletion
- Client disconnect during SSE

### Deployment smoke

Health/readiness -> signup/login -> create job -> wait for succeeded -> ask a symbol-specific question -> verify structured file/line citation -> restart worker/web -> verify project and retrieval still work -> force a failed job -> retry successfully.

## Recommended implementation order

1. Make ingestion read-only; remove the commenter mutation and demo-specific debugger patch.
2. Add exact file/security filters and global scan/tree/ZIP/Git limits.
3. Add migrations plus durable source revisions, jobs, events, and terminal-state guarantees.
4. Move ingestion to a dedicated worker with leases, heartbeats, retries, cancellation, and idempotent stages.
5. Replace Chroma-on-ephemeral-disk with Supabase pgvector + PostgreSQL full-text search.
6. Repair provider routing, especially real embedding fallback, budgets, backoff, and circuits.
7. Fix per-turn agent state, structured tool schemas/results, proposal binding, and durable chat checkpointing.
8. Add tests and deployment smoke/failure tests before adding MCP.
9. Add Redis/Valkey only when the durable worker, shared rate limit, lock, or cache needs it.
10. Add read-only MCP tools after service boundaries and authorization are stable.

## Interview-ready design defense

Use this concise explanation:

> The prototype proved the product flow, but the audit found that the original ingestion path coupled HTTP handling, per-file LLM mutation, local Chroma, and final snapshot persistence in one process. That design was not restart-safe and could leave durable rows in processing while losing the uploaded source. I redesigned the boundary around an immutable source revision and a durable Postgres job state machine. A worker claims jobs with leases and heartbeats, stages are idempotent, object storage holds original inputs, and Postgres/pgvector holds searchable state. Redis is optional acceleration, not the source of truth. I kept SSE for chat because the traffic is primarily server-to-client, and I deferred MCP until the internal services and authorization model were stable.

If challenged with “why not MongoDB?”:

> The data has strong relationships and transaction requirements across users, projects, revisions, jobs, files, chunks, and audit records. Postgres already serves that model and pgvector/full-text search cover retrieval. MongoDB would add a second consistency and operations problem without a demonstrated access pattern that needs it.

If challenged with “why Redis?”:

> Redis is useful for queue notification, distributed locks, rate limits, circuit-breaker state, and short-lived shared caches. I do not store authoritative job or project state only in Redis; Postgres allows recovery even if the cache/queue is flushed.

If challenged with “why MCP?”:

> MCP is an interoperability layer for external clients and tools, not an internal job system. I would expose read-only project tree, symbol search, retrieval, and patch-proposal tools over MCP only after those services have stable schemas, tenant isolation, budgets, and audit logging.
