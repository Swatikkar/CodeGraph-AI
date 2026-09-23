import sqlite3
from typing import Annotated, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage
from typing_extensions import NotRequired, TypedDict

from agents.debugger import debugger_node
from agents.supervisor import supervisor_node
from config import settings
from tools.file_ops import get_project_tree, propose_patch, read_project_file
from tools.retriever import retrieve_code_context
from tools.web_search import trusted_web_search
from utils.ai_runtime import AIBudgetExceeded, current_ai_run
from utils.observability import log_event


class ChatState(TypedDict):
    project_name: str
    public_project_name: NotRequired[str]
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
    return _invoke_bounded_tools(state, _supervisor_tools_node)


def debugger_tools_node(state: ChatState):
    return _invoke_bounded_tools(state, _debugger_tools_node)


def _invoke_bounded_tools(state: ChatState, node: ToolNode):
    locked_state = lock_project_tool_args(state)
    messages = locked_state.get("messages", [])
    tool_calls = list(getattr(messages[-1], "tool_calls", []) or []) if messages else []
    context = current_ai_run()
    try:
        if context:
            context.consume_tool_calls(tool_calls)
    except AIBudgetExceeded as exc:
        log_event(
            "agent_budget_exhausted",
            trace_id=context.trace_id if context else "unscoped",
            error_type=type(exc).__name__,
            tool_calls=context.tool_calls if context else 0,
        )
        return {"messages": [AIMessage(content="I stopped this investigation because its tool-call safety budget was reached.")]}
    return node.invoke(locked_state)


def _current_turn_messages(messages: list) -> list:
    """Return only messages belonging to the latest user turn."""
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "type", None) == "human":
            return messages[index:]
    return messages


def route_supervisor(state: ChatState) -> Literal["supervisor_tools", "debugger", "__end__"]:
    messages = state.get("messages", [])
    last_message = messages[-1]
    if getattr(last_message, "tool_calls", None):
        # Persisted chat history must not consume the newest request's budget.
        turn_messages = _current_turn_messages(messages)
        tool_messages = [message for message in turn_messages if getattr(message, "type", None) == "tool"]
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
        return "debugger"
    return "__end__"


def route_debugger(state: ChatState) -> Literal["debugger_tools", "__end__"]:
    messages = state.get("messages", [])
    last_message = messages[-1]
    if getattr(last_message, "tool_calls", None):
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


def close_chat_storage() -> None:
    """Release the SQLite checkpointer cleanly during application shutdown."""
    conn.close()
