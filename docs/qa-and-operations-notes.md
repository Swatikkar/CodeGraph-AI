# CodeGraph AI QA And Operations Notes

Last updated: 2026-06-29

## Current Provider Strategy

The app uses role-based model fallback chains. The current goal is fast, low/no-cost local development with visible provider switching.

Active order:

- Supervisor/codebase Q&A: `groq:llama-3.1-8b-instant`, `gemini:gemini-2.5-flash`, `ollama:qwen2.5-coder:3b`, then larger fallbacks.
- Debugger/coding: `groq:openai/gpt-oss-120b`, `gemini:gemini-2.5-flash`, `ollama:qwen2.5-coder:3b`, then backup coding models.
- Commenter/docs: `groq:llama-3.1-8b-instant`, `gemini:gemini-2.5-flash-lite`, `ollama:qwen2.5-coder:3b`.
- Architecture: `gemini:gemini-2.5-flash`, then Ollama/Groq/OpenRouter fallbacks.
- Embeddings: `gemini:gemini-embedding-001`, then HuggingFace/Ollama fallback.

Operational choices:

- `PROVIDER_TIMEOUT_SECONDS=20`
- `PROVIDER_MAX_RETRIES=0`
- `LANGSMITH_TRACING=false` for local speed
- Backend terminal logs provider attempts, failures, elapsed time, and successes with `[model-router]` lines.
- Supervisor and debugger use `trusted_web_search`, not broad unrestricted search.
- Trusted search is bounded to official docs/package/release sources and returns at most three compact results.

## QA Evidence

Provider probes:

- Groq supervisor route passed: `groq:llama-3.1-8b-instant` in about `1.21s`.
- Groq debugger route passed: `groq:openai/gpt-oss-120b` in about `0.89s`.
- Groq commenter route passed: `groq:llama-3.1-8b-instant` in about `0.48s`.
- Gemini architecture route passed: `gemini:gemini-2.5-flash` in about `2.27s`.
- Tool-bound Groq supervisor route passed in about `1.13s`.
- Fallback proof passed: with an intentionally invalid Groq key in-process, the router failed Groq, switched to Gemini, and completed.

Endpoint/negative tests:

- Guardrails block prompt-injection and secret-exfiltration phrases.
- Normal codebase questions are allowed.
- Path traversal via `../` is blocked for project file resolution.
- Unsafe ZIP entries with traversal or absolute paths are blocked.
- Auth-required endpoints reject unauthenticated requests.
- Signup rejects weak passwords.
- Login rejects wrong passwords.
- Issued JWT works for `/api/auth/me`.
- Direct `/api/chat` prompt no longer starts retrieval/search tools.
- External update/version questions use controlled trusted search.
- Hybrid questions use local project context plus trusted documentation search.
- Successful project deletion removes upload, artifact, tree cache, and Chroma paths without recreating a deleted-project `status.json`.
- `/api/chat` returns `text/event-stream` and emits raw SSE events such as `chunk`, `reference`, `response_meta`, and `done`.

Build/compile checks:

- Backend Python compile passed for main routing/security modules.
- Frontend production build passed.

## Security Controls

Implemented:

- Project names are slugified and bounded.
- Project file access resolves paths and checks they stay inside the project root.
- ZIP extraction rejects unsafe member paths before extraction.
- Chat prompts are screened for obvious prompt-injection and secret-exfiltration phrases.
- Signup passwords require 8-128 characters.
- Provider calls log metadata without printing API keys.
- Retrieved code and LLM file-tool reads redact likely secrets before reaching model providers.
- Ingestion redacts likely secrets before embedding future projects.
- Trusted web search prefers official documentation domains and labels secondary sources.

Deletion behavior:

- `status.json` is kept for active, processing, ready, error, or deferred-cleanup projects because the dashboard uses it for progress and state.
- Fully deleted projects should leave no project artifact folder or `status.json`; if cleanup cannot finish because Windows still holds files, the filesystem marker is `.delete_pending` rather than a normal visible artifact.

Important nuance:

- The authenticated UI may still show the user's own source files. The model-facing retrieval/tool paths are the protected surfaces.

## Known Challenges

- Local Ollama is useful as a no-network fallback but can be slower than Groq/Gemini on this laptop.
- Free provider rate limits can appear quickly under repeated QA. The router now makes those failures visible and switches providers.
- Current guardrails are deterministic keyword checks, not a model-based safety classifier.
- Existing Chroma stores created before secret redaction may contain raw chunks internally, but retrieval output is redacted before model use. Re-ingesting projects refreshes the store with redacted chunks.
- The LangGraph chat path may still perform more than one supervisor model call in some streaming cases; direct prompts no longer trigger tools, and supervisor search is capped to prevent repeated web loops.

## Interview Prep Talking Points

- The system separates roles by model chain instead of using one global model.
- Provider fallback is observable at both terminal and SSE/UI levels.
- Security is enforced at multiple layers: auth, path isolation, upload validation, ZIP extraction safety, prompt guardrails, and secret redaction before provider calls.
- Retrieval uses embeddings plus keyword reranking and caches semantically similar queries.
- Tool use is constrained by role: supervisor uses retrieval/tree/trusted-search tools, debugger can retrieve/search/read/propose patches, and write happens only after user approval.
- The app favors cheap fast routes first, with larger models reserved as fallbacks or specialized routes.
- Interview examples:
  - Local-only: "Explain Map.js" should use retrieval and cite project files.
  - External-docs: "What changed in current React hooks docs?" should use trusted search and cite official docs.
  - Hybrid: "Compare the React used here with current Next.js updates" should inspect project/package context and cite trusted docs.

## Retest Commands

Backend compile:

```powershell
cd backend
.\agentenv\Scripts\python.exe -m py_compile main.py graph_chat.py agents\supervisor.py providers\router.py utils\guardrails.py utils\auth.py utils\secrets.py tools\retriever.py tools\file_ops.py tools\ingester.py agents\commenter.py
```

Frontend build:

```powershell
cd frontend
npm run build
```

Run app:

```powershell
cd backend
agentenv/Scripts/activate
python main.py
```

`python main.py` applies safe reload rules: backend source folders are watched, while generated folders such as `uploads`, `chroma_db`, `project_trees`, `artifacts`, `data`, and the virtual environment are excluded from reload watching.

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```
