import json
import uuid

from langchain_core.messages import SystemMessage

from providers import model_router
from tools.file_ops import propose_patch, read_project_file
from tools.retriever import retrieve_code_context
from tools.web_search import trusted_web_search


DEBUGGER_SYSTEM_PROMPT_TEMPLATE = """You are the Debugger Agent for CodeGraph AI.

ACTIVE PROJECT NAMESPACE: `{project_name}`

Protocol:
1. Investigate with retrieval and exact file reads.
2. Use trusted_web_search for external errors, official library docs, migrations, deprecations, or version-specific API changes.
3. If a file change is needed, call propose_patch. Do not claim the file was written.
4. The user must approve before backend writes any patch.
5. Keep fixes scoped and cite the files you inspected.
"""


def debugger_node(state: dict) -> dict:
    messages = state.get("messages", [])
    project_name = state.get("project_name", "")
    clean_messages = [m for m in messages if "ROUTE_TO_DEBUGGER" not in str(getattr(m, "content", ""))]
    tools = [retrieve_code_context, trusted_web_search, read_project_file, propose_patch]

    response, metadata = model_router.invoke_chat(
        "debugger_coding",
        [SystemMessage(content=DEBUGGER_SYSTEM_PROMPT_TEMPLATE.format(project_name=project_name))] + clean_messages,
        tools=tools,
    )

    if not getattr(response, "tool_calls", None) and str(response.content).strip().startswith("{"):
        try:
            parsed = json.loads(str(response.content).strip())
            if "name" in parsed and "arguments" in parsed:
                response.tool_calls = [{
                    "name": parsed["name"],
                    "args": parsed["arguments"],
                    "id": f"call_{uuid.uuid4().hex}",
                }]
                response.content = ""
        except json.JSONDecodeError:
            pass

    return {"messages": [response], "provider_events": [metadata]}
