
import json
import logging
import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.services.task_service import (
    TaskNotFoundError,
    create_task,
    delete_task,
    list_tasks,
    resolve_task_reference,
    search_tasks,
    serialize_task,
    update_task,
)
from app.schemas.task import TaskCreate, TaskUpdate

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "to do": "todo",
    "todo": "todo",
    "in progress": "in_progress",
    "in_progress": "in_progress",
    "doing": "in_progress",
    "done": "done",
    "complete": "done",
    "completed": "done",
}

PRIORITY_MAP = {
    "low": "low",
    "medium": "medium",
    "normal": "medium",
    "high": "high",
}

STATUS_PATTERN = r"in progress|in_progress|to do|todo|done|complete|completed"
PRIORITY_PATTERN = r"low|medium|high"

ALLOWED_CHANGES = {
    "title",
    "description",
    "status",
    "priority",
    "due_date",
}



def _fallback_parse(message: str) -> dict:
    """Parse common task commands without requiring the OpenAI API."""

    text = message.strip()
    lower = text.lower()

    result = {
        "action": "unknown",
        "title": "",
        "description": "",
        "status": None,
        "priority": None,
        "source_priority": None,
        "due_date": None,
        "query": "",
        "task_ref": "",
        "changes": {},
        "reply": "",
    }

    # DELETE TASK
    delete_match = re.match(
        r"^(?:please\s+)?(?:delete|remove)\s+"
        r"(?:(?:the\s+)?task\s+)?(.+?)[.!?]*$",
        text,
        re.I,
    )

    if delete_match:
        result["action"] = "delete"
        result["task_ref"] = delete_match.group(1).strip(" .!?'\"")
        result["task_ref"] = re.sub(
            r"\s+task$",
            "",
            result["task_ref"],
            flags=re.I,
        ).strip()
        return result

    # CREATE TASK
    create_match = re.match(
        r"^(?:please\s+)?(?:create|add|make)\s+"
        r"(?:(?:a|an|new)\s+)*(?:task\s+)?(?:to\s+)?(.+?)[.!?]*$",
        text,
        re.I,
    )

    if create_match or re.match(r"^remind me to\s+", text, re.I):
        result["action"] = "create"

        title = (
            create_match.group(1).strip()
            if create_match
            else re.sub(
                r"^remind me to\s+",
                "",
                text,
                flags=re.I,
            ).strip(" .!?")
        )

        for phrase, priority in PRIORITY_MAP.items():
            if re.search(
                rf"\b{re.escape(phrase)}[-\s]+priority\b",
                lower,
            ):
                result["priority"] = priority
                title = re.sub(
                    rf"\b{re.escape(phrase)}[-\s]+priority\b",
                    "",
                    title,
                    flags=re.I,
                )

        if re.search(r"\btomorrow\b", lower):
            result["due_date"] = (
                date.today() + timedelta(days=1)
            ).isoformat()
            title = re.sub(
                r"\b(?:by\s+)?tomorrow\b",
                "",
                title,
                flags=re.I,
            )

        result["title"] = title.strip(" .!?")
        return result

    # LIST OR SEARCH
    if any(
        phrase in lower
        for phrase in (
            "show",
            "list",
            "find",
            "search",
            "what are",
            "which tasks",
        )
    ):
        result["action"] = (
            "search"
            if any(
                word in lower
                for word in (
                    "related to",
                    "about",
                    "containing",
                    "find",
                    "search",
                )
            )
            else "list"
        )

        result["query"] = re.sub(
            r"^(?:please\s+)?(?:show|list|find|search)\s+"
            r"(?:me\s+)?(?:all\s+)?(?:the\s+)?(?:tasks\s+)?"
            r"(?:related to\s+|about\s+|for\s+)?",
            "",
            text,
            flags=re.I,
        ).strip(" .!?")

        if "unfinished" in lower or "incomplete" in lower:
            result["status"] = "todo"

        return result

    # UPDATE OR COMPLETE TASK
    update_start = re.match(
        r"^(?:please\s+)?"
        r"(?:mark|update|change|move|complete|finish)\s+"
        r"(?:the\s+)?(.+?)[.!?]*$",
        text,
        re.I,
    )

    if update_start:
        result["action"] = (
            "complete"
            if re.match(r"^(?:complete|finish)\b", text, re.I)
            else "update"
        )

        command_text = update_start.group(1).strip(" .!?")

        # Extract the NEW priority first.
        target_priority_match = re.search(
            r"\b(?:into|to|as)\s+"
            r"(low|medium|normal|high)[-\s]+priority"
            r"(?:\s+task)?\s*$",
            command_text,
            re.I,
        )

        if target_priority_match:
            target_word = target_priority_match.group(1).lower()
            result["priority"] = PRIORITY_MAP[target_word]
            result["changes"]["priority"] = PRIORITY_MAP[target_word]

            # Remove the target-priority phrase before parsing the source.
            command_text = command_text[
                :target_priority_match.start()
            ].strip()

        # Extract the SOURCE priority from the remaining command.
        source_priority_match = re.search(
            r"\b(low|medium|normal|high)[-\s]+priority\b",
            command_text,
            re.I,
        )

        if source_priority_match:
            source_word = source_priority_match.group(1).lower()
            result["source_priority"] = PRIORITY_MAP[source_word]

        # Detect status changes such as "mark learn JS as done".
        status_match = re.search(
            rf"\s+(?:as|to)\s+({STATUS_PATTERN})[.!?]*$",
            command_text,
            re.I,
        )

        if status_match:
            status_word = status_match.group(1).lower()
            result["changes"]["status"] = STATUS_MAP[status_word]
            command_text = command_text[:status_match.start()].strip()

        elif result["action"] == "complete":
            result["changes"]["status"] = "done"

        # Remove the source priority phrase from the reference,
        # but preserve the actual task title.
        command_text = re.sub(
            r"\b(?:low|medium|normal|high)[-\s]+priority\s+",
            "",
            command_text,
            count=1,
            flags=re.I,
        )

        # Remove generic task wording, not meaningful title words.
        command_text = re.sub(
            r"^(?:the\s+)?task\s+",
            "",
            command_text,
            flags=re.I,
        )

        result["task_ref"] = command_text.strip(" \t.!?'\"")

        return result

    return result


def detect_requested_priority(message: str) -> str | None:
    """Extract the target priority, not the task's existing priority."""

    match = re.search(
        r"\b(?:into|to|as)\s+"
        r"(low|medium|normal|high)[-\s]+priority\b",
        message,
        re.I,
    )

    if match:
        return PRIORITY_MAP[match.group(1).lower()]

    # A single priority phrase can be treated as the requested priority
    # only when the command clearly expresses a priority change.
    matches = re.findall(
        r"\b(low|medium|normal|high)[-\s]+priority\b",
        message,
        re.I,
    )

    if len(matches) == 1 and re.search(
        r"\b(?:update|change|move|set)\b",
        message,
        re.I,
    ):
        return PRIORITY_MAP[matches[0].lower()]

    return None




def parse_intent(state: dict) -> dict:
    """Parse intent and preserve explicitly stated priorities."""

    message = state["message"]
    fallback = _fallback_parse(message)

    result = fallback.copy()

    if settings.openai_api_key:
        try:
            from openai import OpenAI
            from app.agent.prompts import SYSTEM_PROMPT

            client = OpenAI(api_key=settings.openai_api_key)

            response = client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": message},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )

            parsed = json.loads(
                response.choices[0].message.content or "{}"
            )

            result.update({
                key: value
                for key, value in parsed.items()
                if key in result
            })

            # Prefer fallback's task reference when it has one.
            if fallback.get("task_ref"):
                result["task_ref"] = fallback["task_ref"]

            # Always preserve the explicitly detected source priority.
            result["source_priority"] = fallback.get("source_priority")

        except Exception as exc:
            logger.warning(
                "OpenAI intent parsing failed; using fallback parser: %s",
                exc,
            )

    requested_priority = detect_requested_priority(message)

    if requested_priority is not None:
        result["priority"] = requested_priority
        result["changes"] = dict(result.get("changes") or {})
        result["changes"]["priority"] = requested_priority

    return {"intent": result}




def format_task_details(task: dict) -> str:
    """Format a serialized task for display in agent chat."""

    status = str(task.get("status") or "Unknown")
    status = status.replace("_", " ").title()

    priority = str(task.get("priority") or "Unknown").title()
    task_id = str(task.get("id") or "")

    return (
        f"**{task.get('title') or 'Untitled task'}**\n"
        f"- Description: {task.get('description') or 'No description'}\n"
        f"- Status: {status}\n"
        f"- Priority: {priority}\n"
        f"- Due date: {task.get('due_date') or 'Not set'}\n"
        f"- Task ID: {task_id[:8] or 'Unavailable'}"
    )


def execute_intent(state: dict) -> dict:
    """Execute a parsed task operation and show task details in chat."""

    db: Session = state["db"]
    intent = state.get("intent") or {}
    action = intent.get("action", "unknown")

    pending = state.get("pending_action")
    confirm = bool(state.get("confirm"))

    # --------------------------------------------------
    # HANDLE PENDING DELETE CONFIRMATION
    # --------------------------------------------------
    if pending and pending.get("action") == "delete":
        if not confirm:
            return {
                "reply": (
                    "Deletion was not confirmed. "
                    "The task has been left unchanged."
                ),
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        try:
            # Preserve the task details before deleting it.
            deleted_details = pending.get("task_details") or {
                "id": pending.get("task_id", ""),
                "title": pending.get("title", "Task"),
                "description": "",
                "status": "Unknown",
                "priority": "Unknown",
                "due_date": None,
            }

            delete_task(db, pending["task_id"])

            return {
                "reply": (
                    "Task deleted successfully.\n\n"
                    "Deleted task details:\n\n"
                    f"{format_task_details(deleted_details)}"
                ),
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        except TaskNotFoundError as exc:
            return {
                "reply": str(exc),
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

    # --------------------------------------------------
    # CLARIFY
    # --------------------------------------------------
    if action == "clarify":
        return {
            "reply": (
                intent.get("reply")
                or "Could you clarify what you want me to do?"
            ),
            "tasks": [],
            "pending_action": None,
            "candidate_task_ids": [],
            "awaiting_clarification": False,
        }

    # --------------------------------------------------
    # CREATE
    # --------------------------------------------------
    if action == "create":
        title = str(intent.get("title") or "").strip()

        if not title:
            return {
                "reply": "What should the task title be?",
                "tasks": [],
                "pending_action": None,
            }

        try:
            data = TaskCreate(
                title=title,
                description=intent.get("description") or "",
                status=intent.get("status") or "todo",
                priority=intent.get("priority") or "medium",
                due_date=intent.get("due_date"),
            )

            task = create_task(db, data)
            task_data = serialize_task(task)

            return {
                "reply": (
                    "Task created successfully!\n\n"
                    f"{format_task_details(task_data)}"
                ),
                "tasks": [task_data],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        except Exception as exc:
            logger.exception("Failed to create task")
            return {
                "reply": f"I couldn't create that task: {exc}",
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

    # --------------------------------------------------
    # LIST OR SEARCH
    # --------------------------------------------------
    if action in ("list", "search"):
        query = (intent.get("query") or "").strip()
        status = intent.get("status")

        found = (
            search_tasks(db, query, status=status)
            if action == "search" and query
            else list_tasks(db, status=status)
        )

        tasks = [serialize_task(task) for task in found]

        if not tasks:
            return {
                "reply": "I couldn't find any matching tasks.",
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        # Avoid making the chat excessively long.
        shown_tasks = tasks[:20]

        task_details = "\n\n".join(
            format_task_details(task)
            for task in shown_tasks
        )

        reply = (
            f"Found {len(tasks)} matching task(s). "
            f"Showing {len(shown_tasks)} task(s):\n\n"
            f"{task_details}"
        )

        if len(tasks) > len(shown_tasks):
            reply += (
                "\n\nOnly the first 20 tasks are shown in the chat. "
                "The task board contains the returned task results."
            )

        return {
            "reply": reply,
            "tasks": tasks,
            "pending_action": None,
            "candidate_task_ids": [],
            "awaiting_clarification": False,
        }

    # --------------------------------------------------
    # UPDATE, COMPLETE, OR DELETE
    # --------------------------------------------------
    if action in ("update", "complete", "delete"):
        reference = (intent.get("task_ref") or "").strip()

        if not reference:
            return {
                "reply": (
                    "Which task do you mean? "
                    "Please include part of its title."
                ),
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        matches = resolve_task_reference(db, reference)
        source_priority = intent.get("source_priority")

        if source_priority:
            matches = [
                task
                for task in matches
                if task.priority == source_priority
            ]
                

        if not matches:
            return {
                "reply": (
                    f"I couldn't find a task matching “{reference}”. "
                    "Please try its exact title."
                ),
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        # ----------------------------------------------
        # MULTIPLE MATCHES: ASK USER TO SELECT A TASK
        # ----------------------------------------------
        if len(matches) > 1:
            candidates = matches[:5]
            candidate_data = [
                serialize_task(task)
                for task in candidates
            ]

            options = "\n\n".join(
                format_task_details(task_data)
                for task_data in candidate_data
            )

            return {
                "reply": (
                    f"I found {len(matches)} matching tasks. "
                    "Please select the task you want to "
                    f"{action} by its ID:\n\n{options}"
                ),
                "tasks": candidate_data,
                "pending_action": {
                    "action": action,
                    "task_ref": reference,
                    "changes": dict(intent.get("changes") or {}),
                },
                "candidate_task_ids": [
                    str(task.id)
                    for task in candidates
                ],
                "awaiting_clarification": True,
            }

        task = matches[0]
        task_data = serialize_task(task)

        # ----------------------------------------------
        # DELETE: ASK FOR CONFIRMATION
        # ----------------------------------------------
        if action == "delete":
            return {
                "reply": (
                    "Please confirm that you want to delete this task:\n\n"
                    f"{format_task_details(task_data)}\n\n"
                    'Send "yes, delete it" to confirm, '
                    'or "no" to cancel.'
                ),
                "tasks": [task_data],
                "pending_action": {
                    "action": "delete",
                    "task_id": str(task.id),
                    "title": task.title,
                    "task_details": task_data,
                },
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        # ----------------------------------------------
        # PREPARE UPDATE OR COMPLETE
        # ----------------------------------------------
        changes = dict(intent.get("changes") or {})

        # Apply an explicitly parsed priority change.
        if intent.get("priority") is not None:
            changes["priority"] = intent["priority"]

        # Completing a task means setting its status to done.
        if action == "complete":
            changes["status"] = "done"

        # Only permit supported fields.
        changes = {
            key: value
            for key, value in changes.items()
            if key in ALLOWED_CHANGES and value is not None
        }

        if not changes:
            return {
                "reply": (
                    f"What would you like to change about "
                    f"**{task.title}**?\n\n"
                    f"Current task details:\n\n"
                    f"{format_task_details(task_data)}"
                ),
                "tasks": [task_data],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        # ----------------------------------------------
        # EXECUTE UPDATE OR COMPLETE
        # ----------------------------------------------
        try:
            updated = update_task(
                db,
                task.id,
                TaskUpdate(**changes),
            )

            updated_data = serialize_task(updated)

            if action == "complete":
                success_message = "Task completed successfully!"
            else:
                success_message = "Task updated successfully!"

            return {
                "reply": (
                    f"{success_message}\n\n"
                    f"{format_task_details(updated_data)}"
                ),
                "tasks": [updated_data],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
                "selected_task_id": str(updated.id),
            }

        except Exception as exc:
            logger.exception("Failed to update task")
            return {
                "reply": f"I couldn't update that task: {exc}",
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

    # --------------------------------------------------
    # UNKNOWN ACTION
    # --------------------------------------------------
    return {
        "reply": (
            "I can create, list/search, update, complete, or delete tasks.\n\n"
            "Example: Create a high-priority task to finish the demo tomorrow."
        ),
        "tasks": [],
        "pending_action": None,
        "candidate_task_ids": [],
        "awaiting_clarification": False,
    }


def handle_pending_selection(state: dict) -> dict:
    """Resolve a task-ID selection and resume the pending operation."""

    db: Session = state["db"]
    message = state.get("message", "").strip()
    pending = state.get("pending_action") or {}

    candidate_ids = [
        str(task_id)
        for task_id in state.get("candidate_task_ids", [])
    ]

    # Accept either the displayed short ID or the full task ID.
    id_match = re.search(
        r"(?:\bID\s*[:#]?\s*)?([a-fA-F0-9-]{8,})",
        message,
        re.IGNORECASE,
    )
    supplied_id = id_match.group(1) if id_match else ""

    selected_ids = [
        task_id
        for task_id in candidate_ids
        if (
            task_id.lower() == supplied_id.lower()
            or (
                len(supplied_id) >= 8
                and task_id.lower().startswith(supplied_id.lower())
            )
        )
    ]

    if len(selected_ids) != 1:
        return {
            "reply": (
                "Please reply with one of the task IDs shown above. "
                "No task has been changed."
            ),
            "tasks": state.get("tasks", []),
            "pending_action": pending,
            "candidate_task_ids": candidate_ids,
            "awaiting_clarification": True,
        }

    selected_id = selected_ids[0]

    # Find the selected task. Verify its exact ID after resolving it.
    matches = resolve_task_reference(db, selected_id)
    task = next(
        (
            item
            for item in matches
            if str(item.id).lower() == selected_id.lower()
        ),
        None,
    )

    if task is None:
        return {
            "reply": (
                "That task could not be found. Please list the tasks "
                "again and start a new request."
            ),
            "tasks": [],
            "pending_action": None,
            "candidate_task_ids": [],
            "awaiting_clarification": False,
        }

    action = pending.get("action")
    changes = dict(pending.get("changes") or {})

    try:
        # A selected delete still requires explicit confirmation.
        if action == "delete":
            return {
                "reply": (
                    f"You selected **{task.title}** "
                    f"(ID {str(task.id)[:8]}). "
                    'To delete it, reply "yes, delete it".'
                ),
                "tasks": [serialize_task(task)],
                "pending_action": {
                    "action": "delete",
                    "task_id": str(task.id),
                    "title": task.title,
                },
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        if action == "complete":
            changes["status"] = "done"

        if action == "update":
            changes = {
                key: value
                for key, value in changes.items()
                if key in ALLOWED_CHANGES and value is not None
            }

        if action not in {"complete", "update"}:
            return {
                "reply": (
                    "The pending operation is not supported. "
                    "Please start a new request."
                ),
                "tasks": [],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        if not changes:
            return {
                "reply": (
                    f"You selected **{task.title}**. "
                    "What would you like to change?"
                ),
                "tasks": [serialize_task(task)],
                "pending_action": None,
                "candidate_task_ids": [],
                "awaiting_clarification": False,
            }

        updated = update_task(
            db,
            task.id,
            TaskUpdate(**changes),
        )

        return {
            "reply": (
                f"Updated **{updated.title}** "
                f"(ID {str(updated.id)[:8]}) successfully."
            ),
            "tasks": [serialize_task(updated)],
            "pending_action": None,
            "candidate_task_ids": [],
            "awaiting_clarification": False,
            "selected_task_id": str(updated.id),
        }

    except TaskNotFoundError as exc:
        return {
            "reply": str(exc),
            "tasks": [],
            "pending_action": None,
            "candidate_task_ids": [],
            "awaiting_clarification": False,
        }

    except Exception:
        logger.exception("Failed to execute selected task operation")
        return {
            "reply": (
                "I couldn't complete that operation. "
                "Please try again."
            ),
            "tasks": [],
            "pending_action": None,
            "candidate_task_ids": [],
            "awaiting_clarification": False,
        }
