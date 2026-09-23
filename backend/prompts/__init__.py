from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    version: str
    purpose: str


PROMPT_CATALOG = {
    "supervisor_reasoning": PromptSpec("supervisor.codebase_answer", "2026-09-23.1", "Answer and route codebase questions."),
    "debugger_coding": PromptSpec("debugger.investigate_patch", "2026-09-23.1", "Investigate bugs and propose approval-gated patches."),
    "commenter_code_docs": PromptSpec("ingestion.code_documentation", "2026-09-23.1", "Optionally document source without changing behavior."),
    "architecture_design": PromptSpec("ingestion.architecture", "2026-09-23.1", "Generate a bounded architecture diagram."),
    "retrieval_query": PromptSpec("retrieval.query_rewrite", "2026-09-23.1", "Rewrite retrieval queries when enabled."),
    "vision_error_analysis": PromptSpec("vision.error_analysis", "2026-09-23.1", "Analyze an uploaded error screenshot."),
    "guardrail": PromptSpec("guardrail.prompt_screen", "2026-09-23.1", "Classify unsafe user requests."),
}


def prompt_spec_for(role: str) -> PromptSpec:
    return PROMPT_CATALOG.get(role, PromptSpec(f"unregistered.{role}", "0", "Unregistered prompt role."))
