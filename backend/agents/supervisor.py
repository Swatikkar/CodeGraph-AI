import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from providers import model_router
from tools.file_ops import get_project_tree
from tools.retriever import retrieve_code_context
from tools.web_search import trusted_web_search


SUPERVISOR_SYSTEM_PROMPT_TEMPLATE = """You are the Supervisor Agent for CodeGraph AI.

ACTIVE PROJECT NAMESPACE: `{project_name}`

Your job:
- Answer codebase questions using retrieval and project tree tools.
- For greetings, direct response tests, or non-codebase questions, answer directly without tools.
- Use trusted_web_search for latest/current/update/version/migration/deprecated/import-change/framework-comparison questions.
- For hybrid questions, use project tools for the local code and trusted_web_search for current external docs.
- If the user asks to modify/fix/write code, respond exactly: ROUTE_TO_DEBUGGER
- If the user references screenshots/images, ask for or use vision analysis when available.
- Cite files from retrieval context when answering project questions.
- Cite trusted documentation URLs when answering external tech-update questions.

Never access projects outside `{project_name}`.
Use at most one trusted_web_search call per answer.
"""


def _has_xml_tool_call(content: str) -> bool:
    return bool(re.search(r"<function>|</function>|<tool_call>|</tool_call>", content or ""))


def supervisor_node(state: dict) -> dict:
    messages = state.get("messages", [])
    project_name = state.get("project_name", "")
    system_prompt = SUPERVISOR_SYSTEM_PROMPT_TEMPLATE.format(project_name=project_name)
    tools = [retrieve_code_context, get_project_tree, trusted_web_search]
    last_human = next((m for m in reversed(messages) if getattr(m, "type", None) == "human"), None)
    prompt = str(getattr(last_human, "content", "") if last_human else "").lower()
    code_markers = [
        "file", "function", "class", "method", "code", "bug", "error", "cite",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", "project", "where", "what does",
        "component", "architecture", "dependency", "import", "map.js", "pathbutton",
    ]
    external_markers = [
        "latest", "current", "update", "updated", "version", "release", "migration",
        "migrate", "deprecated", "deprecation", "breaking change", "changelog",
        "docs", "documentation", "official", "compare", "next js", "next.js",
        "react", "vite", "fastapi", "langchain", "pydantic", "sqlalchemy",
        "import changed", "new method", "new api", "library",
    ]
    direct_markers = ["reply with exactly", "say exactly", "stream ok"]
    debugger_markers = [
        "debug", "fix", "runtime error", "traceback", "stack trace", "exception",
        "cannot read properties", "undefined", "crash", "failing", "broken",
    ]
    should_use_retrieval = last_human and any(marker in prompt for marker in code_markers)
    should_use_search = last_human and any(marker in prompt for marker in external_markers)
    is_direct_prompt = (
        any(marker in prompt for marker in direct_markers)
        or prompt.strip() in {"hello", "hi", "hey"}
    )
    if last_human and not is_direct_prompt and any(marker in prompt for marker in debugger_markers):
        print("[agent-router] Supervisor routed query to Debugger Agent", flush=True)
        return {"messages": [AIMessage(content="ROUTE_TO_DEBUGGER")], "provider_events": []}

    should_use_tools = (should_use_retrieval or should_use_search) and not is_direct_prompt
    has_tool_results = any(getattr(message, "type", None) == "tool" for message in messages)

    response, metadata = model_router.invoke_chat(
        "supervisor_reasoning",
        [
            SystemMessage(
                content=(
                    system_prompt
                    + ("\nTool results are already available. Answer now without calling another tool." if has_tool_results else "")
                )
            )
        ] + messages,
        tools=tools if should_use_tools and not has_tool_results else None,
    )

    if _has_xml_tool_call(str(response.content)) and not getattr(response, "tool_calls", None):
        last_human = next((m for m in reversed(messages) if getattr(m, "type", None) == "human"), None)
        if last_human:
            response, metadata = model_router.invoke_chat(
                "supervisor_reasoning",
                [SystemMessage(content=system_prompt), last_human],
                tools=tools if should_use_tools else None,
            )

    should_force_retrieval = (
        last_human
        and should_use_tools
        and should_use_retrieval
        and not getattr(response, "tool_calls", None)
        and any(marker in str(last_human.content).lower() for marker in code_markers)
    )

    if should_force_retrieval:
        if last_human:
            context = retrieve_code_context.invoke({
                "query": str(last_human.content),
                "project_name": project_name,
                "top_n": 5,
            })
            response, metadata = model_router.invoke_chat(
                "supervisor_reasoning",
                [
                    SystemMessage(content=system_prompt + "\nUse the provided retrieved context. Do not ask for namespace."),
                    HumanMessage(content=f"User question:\n{last_human.content}\n\nRetrieved context:\n{context}"),
                ],
            )

    return {"messages": [response], "provider_events": [metadata]}
