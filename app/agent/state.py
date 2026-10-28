from typing import Any, TypedDict
from sqlalchemy.orm import Session


class AgentState(TypedDict, total=False):
    message: str
    db: Session
    confirm: bool
    pending_action: dict[str, Any] | None
    intent: dict[str, Any]
    intents: list[dict[str, Any]]
    reply: str
    tasks: list[dict[str, Any]]
    next_action: str
    candidate_task_ids: list[str]
    selected_task_id: str | None
    awaiting_clarification: bool
    conversation_id: str | None
    history: list[dict]