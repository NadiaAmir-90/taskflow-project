from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskStatus = Literal["todo", "in_progress", "done"]
TaskPriority = Literal["low", "medium", "high"]


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Title cannot be empty or whitespace.")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("Title cannot be empty or whitespace.")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class TaskRead(BaseModel):
    id: str
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    due_date: date | None
    created_at: str
    updated_at: str
    model_config = ConfigDict(from_attributes=True)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    confirm: bool = False
    pending_action: dict | None = None
    candidate_task_ids: list[str] = Field(default_factory=list)
    awaiting_clarification: bool = False

    history: list[dict] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty.")
        return value


class ChatResponse(BaseModel):
    reply: str
    tasks: list[dict] = Field(default_factory=list)
    pending_action: dict | None = None
    candidate_task_ids: list[str] = Field(default_factory=list)
    awaiting_clarification: bool = False
    selected_task_id: str | None = None
