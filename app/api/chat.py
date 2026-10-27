from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agent.graph import run_agent
from app.database.session import get_db
from app.schemas.task import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/chat", tags=["assistant"])


@router.post("", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
):
    message = payload.message.strip()
    normalized_message = message.lower()

    is_confirm = payload.confirm or normalized_message in {
        "yes",
        "yes, delete it",
        "confirm",
        "confirm delete",
        "yes delete it",
    }

    is_cancel = normalized_message in {
        "no",
        "cancel",
        "don't delete",
        "do not delete",
    }

    pending_action = payload.pending_action

    if is_cancel and pending_action:
        action = pending_action.get("action", "operation")

        return ChatResponse(
            reply=f"Cancelled the pending {action} operation. No changes were made.",
            tasks=[],
            pending_action=None,
            awaiting_clarification=False,
            candidate_task_ids=[],
            selected_task_id=None,
        )

    result = run_agent(
        message=message,
        db=db,
        confirm=is_confirm,
        pending_action=pending_action,
        awaiting_clarification=getattr(
            payload, "awaiting_clarification", False
        ),
        candidate_task_ids=getattr(
            payload, "candidate_task_ids", []
        ),
    )

    return ChatResponse(**result)
