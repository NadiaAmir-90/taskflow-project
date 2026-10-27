from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    execute_intent,
    parse_intent,
    handle_pending_selection,
)
from app.agent.state import AgentState


def route_after_start(state: AgentState) -> str:
    """Route selection replies before normal intent parsing."""
    pending = state.get("pending_action")

    if pending and state.get("awaiting_clarification", False):
        return "handle_pending_selection"

    return "parse_intent"


builder = StateGraph(AgentState)

builder.add_node("parse_intent", parse_intent)
builder.add_node("execute_intent", execute_intent)
builder.add_node("handle_pending_selection", handle_pending_selection)

builder.add_conditional_edges(
    START,
    route_after_start,
    {
        "handle_pending_selection": "handle_pending_selection",
        "parse_intent": "parse_intent",
    },
)

builder.add_edge("parse_intent", "execute_intent")
builder.add_edge("execute_intent", END)
builder.add_edge("handle_pending_selection", END)

agent_graph = builder.compile()


def run_agent(
    message: str,
    db,
    confirm: bool = False,
    pending_action: dict | None = None,
    awaiting_clarification: bool = False,
    candidate_task_ids: list[str] | None = None,
) -> dict:
    result = agent_graph.invoke(
        {
            "message": message,
            "db": db,
            "confirm": confirm,
            "pending_action": pending_action,
            "awaiting_clarification": awaiting_clarification,
            "candidate_task_ids": candidate_task_ids or [],
        }
    )

    return {
        "reply": result.get("reply", "Done."),
        "tasks": result.get("tasks", []),
        "pending_action": result.get("pending_action"),
        "awaiting_clarification": result.get(
            "awaiting_clarification", False
        ),
        "candidate_task_ids": result.get("candidate_task_ids", []),
        "selected_task_id": result.get("selected_task_id"),
    }
