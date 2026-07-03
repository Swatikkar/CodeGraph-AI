import sqlite3
from typing import Annotated, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from agents.debugger import debugger_node
from agents.supervisor import supervisor_node
from config import settings
from tools.file_ops import get_project_tree, propose_patch, read_project_file
from tools.retriever import retrieve_code_context
from tools.web_search import trusted_web_search


class ChatState(TypedDict):
    project_name: str
    messages: Annotated[list, add_messages]
    provider_events: list


PROJECT_SCOPED_TOOLS = {
    "retrieve_code_context",
    "get_project_tree",
    "read_project_file",
    "propose_patch",
}


def lock_project_tool_args(state: ChatState) -> ChatState:
    """Keep project-scoped tool calls bound to the authenticated project namespace."""
    project_name = state.get("project_name", "")
    messages = state.get("messages", [])
    if not project_name or not messages:
        return state

    last_message = messages[-1]
    for tool_call in getattr(last_message, "tool_calls", []) or []:
        if tool_call.get("name") in PROJECT_SCOPED_TOOLS:
            args = tool_call.setdefault("args", {})
            if isinstance(args, dict):
                args["project_name"] = project_name
    return state


_supervisor_tools_node = ToolNode([retrieve_code_context, get_project_tree, trusted_web_search])
_debugger_tools_node = ToolNode([retrieve_code_context, trusted_web_search, read_project_file, propose_patch])


def supervisor_tools_node(state: ChatState):
    return _supervisor_tools_node.invoke(lock_project_tool_args(state))


def debugger_tools_node(state: ChatState):
    return _debugger_tools_node.invoke(lock_project_tool_args(state))


def route_supervisor(state: ChatState) -> Literal["supervisor_tools", "debugger", "__end__"]:
    messages = state.get("messages", [])
    last_message = messages[-1]
    if getattr(last_message, "tool_calls", None):
        tool_names = ", ".join(tool_call.get("name", "tool") for tool_call in getattr(last_message, "tool_calls", []))
        print(f"[agent-router] Supervisor selected retrieval/tools: {tool_names}", flush=True)
        tool_messages = [message for message in messages if getattr(message, "type", None) == "tool"]
        if len(tool_messages) >= 2:
            return "__end__"
        trusted_search_count = sum(
            1
            for message in tool_messages
            if getattr(message, "name", "") == "trusted_web_search"
            or "Trusted web search results for:" in str(getattr(message, "content", ""))
        )
        requested_search_count = sum(
            1
            for tool_call in getattr(last_message, "tool_calls", [])
            if tool_call.get("name") == "trusted_web_search"
        )
        if trusted_search_count >= 1 and requested_search_count:
            return "__end__"
        return "supervisor_tools"
    if "ROUTE_TO_DEBUGGER" in str(last_message.content):
        print("[agent-router] Supervisor routed query to Debugger Agent", flush=True)
        return "debugger"
    return "__end__"


def route_debugger(state: ChatState) -> Literal["debugger_tools", "__end__"]:
    messages = state.get("messages", [])
    last_message = messages[-1]
    if getattr(last_message, "tool_calls", None):
        tool_names = ", ".join(tool_call.get("name", "tool") for tool_call in getattr(last_message, "tool_calls", []))
        print(f"[agent-router] Debugger selected tools: {tool_names}", flush=True)
        return "debugger_tools"
    return "__end__"


builder = StateGraph(ChatState)
builder.add_node("supervisor", supervisor_node)
builder.add_node("debugger", debugger_node)
builder.add_node("supervisor_tools", supervisor_tools_node)
builder.add_node("debugger_tools", debugger_tools_node)

builder.add_edge(START, "supervisor")
builder.add_conditional_edges("supervisor", route_supervisor, {
    "supervisor_tools": "supervisor_tools",
    "debugger": "debugger",
    "__end__": END,
})
builder.add_edge("supervisor_tools", "supervisor")
builder.add_conditional_edges("debugger", route_debugger, {
    "debugger_tools": "debugger_tools",
    "__end__": END,
})
builder.add_edge("debugger_tools", "debugger")

settings.create_required_directories()
conn = sqlite3.connect(settings.SQLITE_DIR / "chat_history.db", check_same_thread=False)
memory = SqliteSaver(conn)

chat_graph_app = builder.compile(checkpointer=memory)
