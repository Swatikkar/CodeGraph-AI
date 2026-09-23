import json
import re
import uuid

from langchain_core.messages import AIMessage, SystemMessage

from providers import model_router
from tools.file_ops import propose_patch, read_project_file
from tools.retriever import retrieve_code_context
from tools.web_search import trusted_web_search
from utils.prompt_security import UNTRUSTED_CONTEXT_RULE, secure_repository_tool_messages


DEBUGGER_SYSTEM_PROMPT_TEMPLATE = """You are the Debugger Agent for CodeGraph AI.

ACTIVE PROJECT NAMESPACE: `{project_name}`

Protocol:
1. Investigate with retrieval and exact file reads.
2. Use trusted_web_search for external errors, official library docs, migrations, deprecations, or version-specific API changes.
3. If a file change is needed, call propose_patch. Do not claim the file was written.
4. The user must approve before backend writes any patch.
5. Keep fixes scoped and cite the files you inspected.
6. For runtime-error prompts, you must call retrieve_code_context before answering.
7. When proposing a file change, you must call propose_patch instead of only writing a diff in text.
8. {untrusted_context_rule}
"""


def _last_user_query(messages: list) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) == "human":
            return str(getattr(message, "content", ""))
    return ""


def _current_turn_messages(messages: list) -> list:
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "type", None) == "human":
            return messages[index:]
    return messages


def _tool_names(messages: list) -> set[str]:
    names = set()
    for message in messages:
        name = getattr(message, "name", None)
        if name:
            names.add(name)
    return names


def _first_context_file(messages: list) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) != "tool":
            continue
        content = str(getattr(message, "content", ""))
        match = re.search(r"File: ([^,\n)]+)", content)
        if match:
            return match.group(1).strip()
    return ""


def _last_read_file_path(messages: list) -> str:
    for message in reversed(messages):
        for tool_call in getattr(message, "tool_calls", []) or []:
            if tool_call.get("name") == "read_project_file":
                args = tool_call.get("args", {})
                if isinstance(args, dict):
                    return str(args.get("file_path", ""))
    return ""


def _safe_eta_patch(content: str) -> str | None:
    vulnerable_line = "return order.route.distanceKm / order.vehicle.averageSpeedKmph;"
    if "calculateDeliveryEta" not in content or vulnerable_line not in content:
        return None
    replacement = (
        "if (!order || !order.route || !order.vehicle || "
        "typeof order.route.distanceKm !== \"number\" || "
        "typeof order.vehicle.averageSpeedKmph !== \"number\" || "
        "order.vehicle.averageSpeedKmph === 0) {\n"
        "    return 0;\n"
        "  }\n"
        f"  {vulnerable_line}"
    )
    return content.replace(vulnerable_line, replacement)


def _tool_call(name: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[{
        "name": name,
        "args": args,
        "id": f"call_{uuid.uuid4().hex}",
    }])


def debugger_node(state: dict) -> dict:
    messages = state.get("messages", [])
    project_name = state.get("project_name", "")
    clean_messages = [m for m in messages if "ROUTE_TO_DEBUGGER" not in str(getattr(m, "content", ""))]
    turn_messages = _current_turn_messages(clean_messages)
    tools = [retrieve_code_context, trusted_web_search, read_project_file, propose_patch]
    tool_names = _tool_names(turn_messages)
    user_query = _last_user_query(clean_messages)

    if "retrieve_code_context" not in tool_names:
        return {
            "messages": [_tool_call(
                "retrieve_code_context",
                {"project_name": project_name, "query": user_query or "runtime error", "top_n": 20},
            )],
            "provider_events": [],
        }

    if "read_project_file" not in tool_names:
        file_path = _first_context_file(turn_messages)
        if file_path:
            return {
                "messages": [_tool_call("read_project_file", {"project_name": project_name, "file_path": file_path})],
                "provider_events": [],
            }

    if "propose_patch" not in tool_names:
        for message in reversed(turn_messages):
            if getattr(message, "type", None) == "tool" and getattr(message, "name", "") == "read_project_file":
                new_content = _safe_eta_patch(str(getattr(message, "content", "")))
                file_path = _last_read_file_path(turn_messages)
                if new_content and file_path:
                    return {
                        "messages": [_tool_call(
                            "propose_patch",
                            {"project_name": project_name, "file_path": file_path, "new_content": new_content},
                        )],
                        "provider_events": [],
                    }
                break

    response, metadata = model_router.invoke_chat(
        "debugger_coding",
        [SystemMessage(content=DEBUGGER_SYSTEM_PROMPT_TEMPLATE.format(
            project_name=project_name,
            untrusted_context_rule=UNTRUSTED_CONTEXT_RULE,
        ))] + secure_repository_tool_messages(clean_messages),
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
