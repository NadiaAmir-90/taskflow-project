from datetime import date, datetime, timezone
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.models import Task
from app.database.vector_store import vector_store
from app.schemas.task import TaskCreate, TaskUpdate

class TaskNotFoundError(Exception):
    pass

def serialize_task(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description or "",
        "status": task.status,
        "priority": task.priority,
        "due_date": task.due_date,
        "created_at": task.created_at.isoformat() if task.created_at else "",
        "updated_at": task.updated_at.isoformat() if task.updated_at else "",
    }

def list_tasks(db: Session, status: str | None = None, priority: str | None = None) -> list[Task]:
    stmt = select(Task).order_by(Task.created_at.desc())
    if status:
        stmt = stmt.where(Task.status == status)
    if priority:
        stmt = stmt.where(Task.priority == priority)
    return list(db.scalars(stmt).all())

def get_task(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise TaskNotFoundError(f"No task found with ID {task_id}.")
    return task

def create_task(db: Session, data: TaskCreate) -> Task:
    task = Task(id=str(uuid4()), title=data.title, description=data.description, status=data.status,
                priority=data.priority, due_date=data.due_date.isoformat() if data.due_date else None)
    db.add(task)
    db.commit()
    db.refresh(task)
    vector_store.upsert(task)
    return task

def update_task(db: Session, task_id: str, data: TaskUpdate) -> Task:
    task = get_task(db, task_id)
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field == "due_date":
            value = value.isoformat() if value else None
        setattr(task, field, value)
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    vector_store.upsert(task)
    return task

def delete_task(db: Session, task_id: str) -> None:
    task = get_task(db, task_id)
    db.delete(task)
    db.commit()
    vector_store.delete(task_id)

def search_tasks(db: Session, query: str, status: str | None = None) -> list[Task]:
    ids = vector_store.search(query, limit=30)
    by_id = {task.id: task for task in db.scalars(select(Task).where(Task.id.in_(ids))).all()} if ids else {}
    ordered = [by_id[task_id] for task_id in ids if task_id in by_id]
    # Include lexical results so new/unindexed tasks remain findable.
    words = [word for word in query.lower().split() if len(word) > 1]
    for task in list_tasks(db, status=status):
        if task.id not in {item.id for item in ordered} and any(word in (task.title + " " + task.description).lower() for word in words):
            ordered.append(task)
    if status:
        ordered = [task for task in ordered if task.status == status]
    return ordered


def resolve_task_reference(db: Session, reference: str) -> list[Task]:
    # Normalize the task reference
    reference = reference.strip().lower()

    # Remove matching surrounding quotes
    if len(reference) >= 2:
        if (
            (reference[0] == '"' and reference[-1] == '"')
            or (reference[0] == "'" and reference[-1] == "'")
        ):
            reference = reference[1:-1].strip()

    if not reference:
        return []

    tasks = list_tasks(db)

    # 1. Match task ID
    exact_id = [
        task for task in tasks
        if task.id.lower() == reference
    ]
    if exact_id:
        return exact_id

    # 2. Match exact task title
    exact_title = [
        task for task in tasks
        if task.title.strip().lower() == reference
    ]
    if exact_title:
        return exact_title

    # 3. Match title containing the reference
    phrase_matches = [
        task for task in tasks
        if reference in task.title.strip().lower()
    ]
    if phrase_matches:
        return phrase_matches

    # 4. Match all meaningful words in the title/description
    tokens = [
        word for word in reference.split()
        if len(word) > 2
    ]

    matches = [
        task for task in tasks
        if tokens
        and all(
            word in f"{task.title} {task.description or ''}".lower()
            for word in tokens
        )
    ]

    return matches

