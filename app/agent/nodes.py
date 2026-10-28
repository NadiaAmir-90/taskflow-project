import json
import logging
import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.agent.prompts import MAX_ACTIONS, build_system_prompt
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

ALLOWED_ACTIONS = {
    "create",
    "list",
    "search",
    "update",
    "delete",
    "complete",
    "chat",
    "clarify",
    "unknown",
}

# Actions that point at one existing task.
TARGET_ACTIONS = {"update", "complete", "delete"}

CANCEL_WORDS = {
    "no",
    "cancel",
    "stop",
    "never mind",
    "nevermind",
    "don't delete",
    "do not delete",
}

# put a separator between the results of several actions.
REPLY_SEPARATOR = "\n\n———\n\n"

HELP_REPLY = (
    "I can create, list/search, update, complete, or delete tasks, "
    "and you can give me several instructions in one message.\n\n"
    "Example: Create a high-priority task to finish the demo tomorrow, "
    "then show all unfinished tasks."
)




# INTENT HELPERS
def _new_intent(**overrides) -> dict:
    """Return a fresh intent dict with every key present."""

    intent = {
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
    intent.update(overrides)
    return intent


def _normalize_status(value):
    if value is None:
        return None
    return STATUS_MAP.get(str(value).strip().lower(), value)


def _normalize_priority(value):
    if value is None:
        return None
    return PRIORITY_MAP.get(str(value).strip().lower(), value)



# Splitting one message into several commands 
_VERBS = (
    r"(?:please\s+)?(?:create|add|make|remind|mark|move|update|change|set|"
    r"delete|remove|complete|finish|show|list|find|search)"
)

_SPLIT_RE = re.compile(
    r"(?:\s*[;\n]+\s*)"
    r"|(?:,?\s+(?:and\s+)?then\s+)"
    rf"|(?:,\s*(?:and\s+)?(?={_VERBS}\b))"
    rf"|(?:\s+and\s+(?={_VERBS}\b))"
    rf"|(?:(?<=[.!?])\s+(?={_VERBS}\b))",
    re.IGNORECASE,
)

_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:and|then|also|now|next)\s+)+",
    re.IGNORECASE,
)

_GREETING_PREFIX_RE = re.compile(
    r"^((?:hi|hello|hey|thanks|thank you)\b[^.!?\n]*[.!?])\s+(\S.*)$",
    re.IGNORECASE | re.DOTALL,
)


def _split_commands(message: str) -> list[str]:
    """Split a message such as 'do A and then do B' into ['do A', 'do B']."""

    text = message.strip()
    parts: list[str] = []

    greeting = _GREETING_PREFIX_RE.match(text)
    if greeting:
        parts.append(greeting.group(1))
        text = greeting.group(2)

    for piece in _SPLIT_RE.split(text):
        piece = _LEADING_FILLER_RE.sub("", (piece or "").strip(" ,"))
        if piece:
            parts.append(piece)

    return parts or [text]



# Small talk (used by the fallback parser when no LLM is available)
_PLEASANTRY = (
    r"(?:hi|hello|hey|yo|good (?:morning|afternoon|evening)|thanks|thank you|"
    r"ok|okay|great|cool|bye|goodbye|how are you(?: doing)?|"
    r"what can you do|what do you do|help(?: me)?|who are you|"
    r"what is your name|there|taskflow)"
)

_CHAT_RE = re.compile(
    rf"^(?:{_PLEASANTRY}[\s,.!?]*)+$",
    re.IGNORECASE,
)


def _chat_reply(lower: str) -> str:
    if re.search(r"\b(?:thanks|thank you)\b", lower):
        return "You're welcome! Let me know if you want to add or change any tasks."

    if re.search(r"\b(?:bye|goodbye)\b", lower):
        return "Goodbye! Your tasks are saved and will be here when you come back."

    if re.search(r"\b(?:what can you do|what do you do|help|who are you)\b", lower):
        return (
            "I manage your tasks from chat. You can ask me to create tasks, "
            "list or search them, change their status, priority or due date, "
            "complete them, or delete them. You can also give me several "
            "instructions in one message."
        )

    if "how are you" in lower:
        return (
            "I'm doing well, thanks for asking! "
            "What would you like to do with your tasks?"
        )

    return "Hi! What would you like to do with your tasks today?"


# USER INTRODUCTION AND NAME QUESTIONS
_NAME_INTRO_PATTERNS = [
    r"\bmy name is\s+([A-Za-z][A-Za-z '\-]{0,49})",
    r"\bi am\s+([A-Za-z][A-Za-z '\-]{0,49})",
    r"\bi'm\s+([A-Za-z][A-Za-z '\-]{0,49})",
    r"\bcall me\s+([A-Za-z][A-Za-z '\-]{0,49})",
]

_NAME_QUESTION_RE = re.compile(
    r"^\s*(?:what is|what's|tell me)\s+my name\s*[?!.]*\s*$",
    re.IGNORECASE,
)



def _extract_user_name(text: str) -> str | None:
    """Extract a name explicitly provided by the user."""
    patterns = [
        r"\bmy name is\s+([A-Za-z][A-Za-z'-]*)",
        r"\bi am\s+([A-Za-z][A-Za-z'-]*)",
        r"\bi'm\s+([A-Za-z][A-Za-z'-]*)",
        r"\bcall me\s+([A-Za-z][A-Za-z'-]*)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1)
            # Avoid treating conversational phrases as names.
            if name.lower() not in {
                "not", "just", "trying", "looking", "working"
            }:
                return name.capitalize()

    return None


def _handle_personal_context(state: dict) -> dict | None:
    """Answer name questions using explicit user messages in chat history."""
    message = state.get("message", "").strip()
    history = state.get("history", [])

    # Read only messages actually written by the user.
    user_messages = [
        item.get("content", "")
        for item in history
        if isinstance(item, dict)
        and item.get("role") == "user"
        and isinstance(item.get("content"), str)
    ]

    # The current message may itself provide the name.
    name = _extract_user_name(message)

    # or, recover the most recently stated name from user history.
    if not name:
        for previous_message in reversed(user_messages):
            name = _extract_user_name(previous_message)
            if name:
                break

    asks_name = bool(re.search(
        r"\b(what is|what's|tell me|do you know|do you remember)"
        r"\b.{0,35}\b(my name|name)\b"
        r"|\bwho am i\b",
        message,
        re.IGNORECASE,
    ))

    if asks_name:
        if name:
            reply = f"Your name is {name}."
        else:
            reply = "I don't know your name yet. What should I call you?"

        return {
            "intents": [_new_intent(action="chat", reply=reply)],
            "intent": _new_intent(action="chat", reply=reply),
        }

    return None



def _is_name_question(message: str) -> bool:
    """Check whether the user is asking for their name."""

    return bool(_NAME_QUESTION_RE.match(message.strip()))


# FALLBACK PARSER (no API key / LLM failure)
def _fallback_parse(message: str) -> dict:
    """Parse common task commands without requiring the LLM API."""

    text = message.strip()
    lower = text.lower()

    result = _new_intent()


    # USER INTRODUCTION
    user_name = _extract_user_name(text)

    if user_name:
        result["action"] = "chat"
        result["reply"] = (
            f"Nice to meet you, {user_name}! "
            "I'll use your name in this conversation."
        )
        result["user_fact"] = {
            "key": "name",
            "value": user_name,
        }
        return result

    # NAME QUESTION
    if _is_name_question(text):
        result["action"] = "chat"
        result["reply"] = (
            "I don't have your name available in my saved memory yet."
        )
        result["needs_user_memory"] = True
        return result


    # DELETE TASK
    delete_match = re.match(
        r"^(?:please\s+)?(?:delete|remove)\s+" r"(?:(?:the\s+)?task\s+)?(.+?)[.!?]*$",
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

        # "delete the demo task" -> "demo"
        result["task_ref"] = re.sub(
            r"^the\s+",
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

        if re.search(r"\b(?:tomorrow|next day)\b", lower):
            result["due_date"] = (date.today() + timedelta(days=1)).isoformat()
            title = re.sub(
                r"\b(?:by\s+)?(?:tomorrow|next day)\b",
                "",
                title,
                flags=re.I,
            )

        # "high-priority task to finish the demo" -> "finish the demo"
        title = re.sub(
            r"^\s*(?:(?:a|an)\s+)?task\s+(?:(?:to|called|named|titled)\s+)?",
            "",
            title,
            flags=re.I,
        )
        title = " ".join(title.split()).strip(" .!?")

        result["title"] = title[:1].upper() + title[1:]
        return result

    # SMALL TALK (must run before the loose list/search keyword check)
    if _CHAT_RE.match(text):
        result["action"] = "chat"
        result["reply"] = _chat_reply(lower)
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

        # "unfinished" means todo AND in_progress. execute_intent detects the
        return result

    # UPDATE OR COMPLETE TASK
    update_start = re.match(
        r"^(?:please\s+)?"
        r"(mark|update|change|move|set|complete|finish)\s+"
        r"(?:the\s+)?(.+?)[.!?]*$",
        text,
        re.I,
    )

    if update_start:
        verb = update_start.group(1).lower()

        result["action"] = "complete" if verb in ("complete", "finish") else "update"

        command_text = update_start.group(2).strip(" .!?")

        # "change the priority of the API task to Low"
        priority_of_match = re.match(
            r"^(?:the\s+)?priority\s+of\s+(?:the\s+)?(.+?)\s+"
            r"(?:to|into|as)\s+(low|medium|normal|high)(?:[-\s]+priority)?$",
            command_text,
            re.I,
        )

        if priority_of_match:
            target = PRIORITY_MAP[priority_of_match.group(2).lower()]
            result["priority"] = target
            result["changes"]["priority"] = target
            result["task_ref"] = re.sub(
                r"\s+task$",
                "",
                priority_of_match.group(1).strip(" .!?'\""),
                flags=re.I,
            ).strip()
            return result

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
            command_text = command_text[: target_priority_match.start()].strip()

        # Extract the SOURCE priority from the remaining command.
        source_priority_match = re.search(
            r"\b(low|medium|normal|high)[-\s]+priority\b",
            command_text,
            re.I,
        )

        if source_priority_match:
            source_word = source_priority_match.group(1).lower()
            result["source_priority"] = PRIORITY_MAP[source_word]

        # Detect status changes such as "mark learn JS as done",
        preposition = r"(?:(?:as|to|into)\s+)?" if verb == "mark" else r"(?:as|to|into)\s+"
        status_match = re.search(
            rf"\s+{preposition}({STATUS_PATTERN})[.!?]*$",
            command_text,
            re.I,
        )

        if status_match:
            status_word = status_match.group(1).lower()
            result["changes"]["status"] = STATUS_MAP[status_word]
            command_text = command_text[: status_match.start()].strip()

        elif result["action"] == "complete":
            result["changes"]["status"] = "done"

        # Remove the source priority phrase from the reference, but preserve the actual task title.
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
        command_text = re.sub(r"\s+task$", "", command_text, flags=re.I)

        result["task_ref"] = command_text.strip(" \t.!?'\"")

        return result

    return result


def detect_requested_priority(message: str) -> str | None:
    """Extract the target priority, not the task's existing priority."""

    match = re.search(
        r"\b(?:into|to|as)\s+" r"(low|medium|normal|high)[-\s]+priority\b",
        message,
        re.I,
    )

    if match:
        return PRIORITY_MAP[match.group(1).lower()]

    # A single priority phrase can be treated as the requested priority( only when the command clearly expresses a priority change.)
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


# PARSE NODE
def _llm_actions(
    message: str,
    history: list[dict] | None = None,
) -> list[dict]:
    """Ask the LLM to parse the current message using recent chat history."""

    from openai import OpenAI

    client = OpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )

    messages = [
        {"role": "system", "content": build_system_prompt()}
    ]

    # Include up to the five most recent messages.
    for item in (history or [])[-5:]:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")

        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({
                "role": role,
                "content": content,
            })

    # The current message must always be last.
    messages.append({
        "role": "user",
        "content": message,
    })

    response = client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0,
    )

    parsed = json.loads(response.choices[0].message.content or "{}")

    if not isinstance(parsed, dict):
        raise ValueError("The model did not return a JSON object.")

    actions = parsed.get("actions")

    # Accept the old single-object format too.
    if actions is None and "action" in parsed:
        actions = [parsed]

    if not isinstance(actions, list) or not actions:
        raise ValueError("The model returned no actions.")

    return [
        item for item in actions if isinstance(item, dict)
    ][:MAX_ACTIONS]



def _clean_llm_action(raw: dict) -> dict:
    """Keep known keys only and make the values safe to execute."""

    intent = _new_intent()
    intent.update({key: value for key, value in raw.items() if key in intent})

    action = str(intent.get("action") or "unknown").strip().lower()
    intent["action"] = action if action in ALLOWED_ACTIONS else "unknown"

    for key in ("title", "description", "query", "task_ref", "reply"):
        intent[key] = str(intent.get(key) or "").strip()

    intent["status"] = _normalize_status(intent.get("status"))
    intent["priority"] = _normalize_priority(intent.get("priority"))

    changes = intent.get("changes")
    intent["changes"] = dict(changes) if isinstance(changes, dict) else {}


    if isinstance(raw.get("user_fact"), dict):
        intent["user_fact"] = raw["user_fact"]

    if raw.get("needs_user_memory"):
        intent["needs_user_memory"] = True


    return intent


def parse_intent(state: dict) -> dict:
    """Parse a message into one or more intents, in the order requested."""

    message = state["message"]

    personal_context_result = _handle_personal_context(state)
    if personal_context_result is not None:
        return personal_context_result
    segments = _split_commands(message)
    fallbacks = [_fallback_parse(segment) for segment in segments]

    intents: list[dict] | None = None
    texts: list[str] = []

    if settings.groq_api_key:
        try:
            raw_actions = _llm_actions(
                message,
                history=state.get("history", []),
            )
            count = len(raw_actions)

            
            aligned = len(segments) == count

            intents = []
            texts = []

            for index, raw in enumerate(raw_actions):
                intent = _clean_llm_action(raw)
                fallback = fallbacks[index] if aligned else None
                text = segments[index] if aligned else (message if count == 1 else "")

                # Prefer the fallback's task reference when it has one.
                if (
                    fallback
                    and fallback["action"] in TARGET_ACTIONS
                    and intent["action"] in TARGET_ACTIONS
                ):
                    if fallback.get("task_ref"):
                        intent["task_ref"] = fallback["task_ref"]

                    # Always preserve the explicitly detected source priority.
                    intent["source_priority"] = fallback.get("source_priority")

                intents.append(intent)
                texts.append(text)

        except Exception as exc:
            logger.warning(
                "LLM intent parsing failed; using fallback parser: %s",
                exc,
            )
            intents = None

    if intents is None:
        intents = fallbacks
        texts = segments

    # Keep an explicitly stated TARGET priority, per action.
    for intent, text in zip(intents, texts):
        if intent["action"] not in ("update", "create") or not text:
            continue

        requested_priority = detect_requested_priority(text)

        if requested_priority is not None:
            intent["priority"] = requested_priority
            intent["changes"] = dict(intent.get("changes") or {})
            intent["changes"]["priority"] = requested_priority

    return {"intents": intents, "intent": intents[0]}



# FORMATTING / RESULT HELPERS
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


def _result(
    reply: str,
    tasks: list | None = None,
    pending: dict | None = None,
    candidates: list | None = None,
    awaiting: bool = False,
    selected: str | None = None,
) -> dict:
    """Result of ONE action."""

    result = {
        "reply": reply,
        "tasks": tasks or [],
        "pending_action": pending,
        "candidate_task_ids": candidates or [],
        "awaiting_clarification": awaiting,
    }

    if selected:
        result["selected_task_id"] = selected

    return result


def _dedupe_tasks(tasks: list[dict]) -> list[dict]:
    """Keep one entry per task id (the latest one), preserving order."""

    latest: dict[str, dict] = {}

    for task in tasks:
        latest[str(task.get("id"))] = task

    return list(latest.values())


def _batch_result(
    replies: list[str],
    tasks: list[dict],
    pending: dict | None = None,
    candidates: list | None = None,
    awaiting: bool = False,
    selected: str | None = None,
) -> dict:
    """Combine the results of several actions into one chat response."""

    result = {
        "reply": REPLY_SEPARATOR.join(reply for reply in replies if reply) or "Done.",
        "tasks": _dedupe_tasks(tasks),
        "pending_action": pending,
        "candidate_task_ids": candidates or [],
        "awaiting_clarification": awaiting,
    }

    if selected:
        result["selected_task_id"] = selected

    return result


def _build_changes(intent: dict, action: str) -> dict:
    """Collect the fields an update/complete should change."""

    changes = dict(intent.get("changes") or {})

    # The model sometimes puts the new value at the top level only.
    if action == "update":
        for key in ("status", "priority", "due_date"):
            if key not in changes and intent.get(key) is not None:
                changes[key] = intent[key]

    # Apply an explicitly parsed priority change.
    if intent.get("priority") is not None and action == "update":
        changes["priority"] = intent["priority"]

    # Completing a task means setting its status to done.
    if action == "complete":
        changes["status"] = "done"

    if "status" in changes:
        changes["status"] = _normalize_status(changes["status"])

    if "priority" in changes:
        changes["priority"] = _normalize_priority(changes["priority"])

    # Only permit supported fields.
    return {
        key: value
        for key, value in changes.items()
        if key in ALLOWED_CHANGES and value is not None
    }



# EXECUTE ONE ACTION
def _execute_single(db: Session, intent: dict) -> dict:
    """Execute ONE parsed task operation."""

    action = intent.get("action", "unknown")


    
   
    # CHAT AND USER MEMORY
    if action == "chat":
        user_fact = intent.get("user_fact")

        if isinstance(user_fact, dict) and user_fact.get("key") == "name":
            name = str(user_fact.get("value") or "").strip()

            if name:
                return _result(
                    f"Nice to meet you, {name}! "
                    "I'll use your name in this conversation."
                )

        if intent.get("needs_user_memory"):
            return _result(
                "I don't have your name available in saved memory yet. "
                "Please introduce yourself again."
            )

        return _result(
            intent.get("reply")
            or "I'm here to help you manage your tasks. What would you like to do?"
        )


    
    # CLARIFY
    if action == "clarify":
        return _result(
            intent.get("reply") or "Could you clarify what you want me to do?"
        )

    
    # CREATE
    if action == "create":
        title = str(intent.get("title") or "").strip()

        if not title:
            return _result("What should the task title be?")

        try:
            data = TaskCreate(
                title=title,
                description=intent.get("description") or "",
                status=_normalize_status(intent.get("status")) or "todo",
                priority=_normalize_priority(intent.get("priority")) or "medium",
                due_date=intent.get("due_date") or None,
            )

            task = create_task(db, data)
            task_data = serialize_task(task)

            return _result(
                "Task created successfully!\n\n" f"{format_task_details(task_data)}",
                tasks=[task_data],
            )

        except Exception as exc:
            logger.exception("Failed to create task")
            return _result(f"I couldn't create that task: {exc}")


    # LIST AND SEARCH TASKS
    if action in ("list", "search"):
        query = (intent.get("query") or "").strip()
        status = intent.get("status")
        priority = intent.get("priority")

        # Detect requests for unfinished tasks.
        query_lower = query.lower()

        unfinished_phrases = (
            "unfinished tasks",
            "incomplete tasks",
            "pending tasks",
            "tasks not completed",
            "not completed tasks",
        )

        unfinished = any(phrase in query_lower for phrase in unfinished_phrases)

        # "Unfinished" already means todo + in_progress, so a single
        # status filter would wrongly hide in-progress tasks.
        if unfinished:
            status = None

        # Remove the unfinished-task phrase to get the actual topic.
        topic = query
        for phrase in unfinished_phrases:
            topic = re.sub(
                re.escape(phrase),
                "",
                topic,
                flags=re.IGNORECASE,
            )

        topic = re.sub(
            r"\b(related to|about|regarding|with)\b",
            " ",
            topic,
            flags=re.IGNORECASE,
        ).strip(" .,!?")

        # Retrieve matching tasks.
        if action == "search" and topic:
            found = search_tasks(
                db,
                topic,
                status=status,
                unfinished=unfinished,
            )
        else:
            found = list_tasks(
                db,
                status=status,
                priority=priority,
                unfinished=unfinished,
            )

        tasks = [serialize_task(task) for task in found]

        if not tasks:
            return _result("I couldn't find any matching tasks.")

        # Avoid making the chat excessively long.
        shown_tasks = tasks[:20]

        task_details = "\n\n".join(format_task_details(task) for task in shown_tasks)

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

        return _result(reply, tasks=tasks)


    # UPDATE, COMPLETE, OR DELETE
    if action in TARGET_ACTIONS:
        reference = (intent.get("task_ref") or "").strip()

        if not reference:
            return _result("Which task do you mean? Please include part of its title.")

        matches = resolve_task_reference(db, reference)
        source_priority = intent.get("source_priority")

        if source_priority:
            matches = [task for task in matches if task.priority == source_priority]

        if not matches:
            return _result(
                f"I couldn't find a task matching “{reference}”. "
                "Please try its exact title."
            )

       
        # MULTIPLE MATCHES: ASK USER TO SELECT A TASK
        if len(matches) > 1:
            candidates = matches[:5]
            candidate_data = [serialize_task(task) for task in candidates]

            options = "\n\n".join(
                format_task_details(task_data) for task_data in candidate_data
            )

            return _result(
                f"I found {len(matches)} matching tasks. "
                "Please select the task you want to "
                f"{action} by its ID:\n\n{options}",
                tasks=candidate_data,
                pending={
                    "action": action,
                    "task_ref": reference,
                    "changes": _build_changes(intent, action),
                },
                candidates=[str(task.id) for task in candidates],
                awaiting=True,
            )

        task = matches[0]
        task_data = serialize_task(task)

       
        # DELETE: ASK FOR CONFIRMATION
        if action == "delete":
            return _result(
                "Please confirm that you want to delete this task:\n\n"
                f"{format_task_details(task_data)}\n\n"
                'Send "yes, delete it" to confirm, '
                'or "no" to cancel.',
                tasks=[task_data],
                pending={
                    "action": "delete",
                    "task_id": str(task.id),
                    "title": task.title,
                    "task_details": task_data,
                },
            )

      
        # PREPARE UPDATE OR COMPLETE
        changes = _build_changes(intent, action)

        if not changes:
            return _result(
                f"What would you like to change about "
                f"**{task.title}**?\n\n"
                f"Current task details:\n\n"
                f"{format_task_details(task_data)}",
                tasks=[task_data],
            )

        
        # EXECUTE UPDATE OR COMPLETE
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

            return _result(
                f"{success_message}\n\n" f"{format_task_details(updated_data)}",
                tasks=[updated_data],
                selected=str(updated.id),
            )

        except Exception as exc:
            logger.exception("Failed to update task")
            return _result(f"I couldn't update that task: {exc}")


    return _result(intent.get("reply") or HELP_REPLY)



# EXECUTE A BATCH OF ACTIONS, ONE AFTER ANOTHER
def _run_batch(
    db: Session,
    intents: list[dict],
    replies: list[str] | None = None,
    tasks: list[dict] | None = None,
    selected: str | None = None,
) -> dict:
    """Run intents in order.

    An action that needs the user (delete confirmation, or choosing between
    several matching tasks) pauses the batch. The actions that have not run yet
    are stored inside pending_action["remaining_intents"] and continue once the
    user has answered.
    """

    replies = list(replies or [])
    tasks = list(tasks or [])

    for index, intent in enumerate(intents):
        result = _execute_single(db, intent)

        replies.append(result["reply"])
        tasks.extend(result["tasks"])
        selected = result.get("selected_task_id") or selected

        pending = result.get("pending_action")

        if pending:
            remaining = intents[index + 1 :]

            if remaining:
                pending = {**pending, "remaining_intents": remaining}

            return _batch_result(
                replies,
                tasks,
                pending=pending,
                candidates=result["candidate_task_ids"],
                awaiting=result["awaiting_clarification"],
                selected=selected,
            )

    return _batch_result(replies, tasks, selected=selected)


def _confirm_pending_delete(db: Session, pending: dict, confirm: bool) -> dict:
    """Handle the user's answer to a delete confirmation, then continue."""

    remaining = list(pending.get("remaining_intents") or [])

    if not confirm:
        return _run_batch(
            db,
            remaining,
            replies=["Deletion was not confirmed. The task has been left unchanged."],
        )

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

        reply = (
            "Task deleted successfully.\n\n"
            "Deleted task details:\n\n"
            f"{format_task_details(deleted_details)}"
        )

    except TaskNotFoundError as exc:
        reply = str(exc)

    return _run_batch(db, remaining, replies=[reply])


def execute_intent(state: dict) -> dict:
    """Execute the parsed task operations, one after another."""

    db: Session = state["db"]

    pending = state.get("pending_action")
    confirm = bool(state.get("confirm"))


    # HANDLE PENDING DELETE CONFIRMATION
    if pending and pending.get("action") == "delete":
        return _confirm_pending_delete(db, pending, confirm)

    intents = state.get("intents")

    if not intents and state.get("intent"):
        intents = [state["intent"]]

    return _run_batch(db, intents or [_new_intent()])



# RESUME AFTER THE USER PICKED ONE OF SEVERAL MATCHING TASKS
def _apply_selection(db: Session, task, pending: dict) -> dict:
    """Run the pending operation on the task the user selected."""

    action = pending.get("action")
    changes = dict(pending.get("changes") or {})

    try:
        # A selected delete still requires explicit confirmation.
        if action == "delete":
            return _result(
                f"You selected **{task.title}** "
                f"(ID {str(task.id)[:8]}). "
                'To delete it, reply "yes, delete it".',
                tasks=[serialize_task(task)],
                pending={
                    "action": "delete",
                    "task_id": str(task.id),
                    "title": task.title,
                },
            )

        if action == "complete":
            changes["status"] = "done"

        if action == "update":
            changes = {
                key: value
                for key, value in changes.items()
                if key in ALLOWED_CHANGES and value is not None
            }

        if action not in {"complete", "update"}:
            return _result(
                "The pending operation is not supported. "
                "Please start a new request."
            )

        if not changes:
            return _result(
                f"You selected **{task.title}**. What would you like to change?",
                tasks=[serialize_task(task)],
            )

        updated = update_task(
            db,
            task.id,
            TaskUpdate(**changes),
        )

        return _result(
            f"Updated **{updated.title}** "
            f"(ID {str(updated.id)[:8]}) successfully.",
            tasks=[serialize_task(updated)],
            selected=str(updated.id),
        )

    except TaskNotFoundError as exc:
        return _result(str(exc))

    except Exception:
        logger.exception("Failed to execute selected task operation")
        return _result("I couldn't complete that operation. Please try again.")


def handle_pending_selection(state: dict) -> dict:
    """Resolve a task-ID selection and resume the pending operation."""

    db: Session = state["db"]
    message = state.get("message", "").strip()
    pending = state.get("pending_action") or {}
    remaining = list(pending.get("remaining_intents") or [])

    candidate_ids = [str(task_id) for task_id in state.get("candidate_task_ids", [])]

    # The user changed their mind: skip this action, continue with the rest.
    if message.lower().strip(" .!") in CANCEL_WORDS:
        return _run_batch(
            db,
            remaining,
            replies=[
                f"Cancelled the pending {pending.get('action', 'operation')} "
                "operation. No changes were made."
            ],
        )

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
        (item for item in matches if str(item.id).lower() == selected_id.lower()),
        None,
    )

    if task is None:
        return _run_batch(
            db,
            remaining,
            replies=[
                "That task could not be found. Please list the tasks "
                "again and start a new request."
            ],
        )

    single = _apply_selection(db, task, pending)
    new_pending = single.get("pending_action")

    # A selected delete still needs confirmation: keep the rest for later.
    if new_pending:
        if remaining:
            single["pending_action"] = {**new_pending, "remaining_intents": remaining}

        return single

    return _run_batch(
        db,
        remaining,
        replies=[single["reply"]],
        tasks=single["tasks"],
        selected=single.get("selected_task_id"),
    )