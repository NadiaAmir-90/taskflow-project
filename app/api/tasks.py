from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.services.task_service import TaskNotFoundError, create_task, delete_task, list_tasks, search_tasks, serialize_task, update_task
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

@router.get("", response_model=list[TaskRead])
def get_tasks(status: str | None = Query(default=None, pattern="^(todo|in_progress|done)$"), priority: str | None = Query(default=None, pattern="^(low|medium|high)$"), db: Session = Depends(get_db)):
    return [serialize_task(task) for task in list_tasks(db, status=status, priority=priority)]

@router.get("/search", response_model=list[TaskRead])
def search(q: str = Query(min_length=1, max_length=300), status: str | None = None, db: Session = Depends(get_db)):
    return [serialize_task(task) for task in search_tasks(db, q, status=status)]

@router.post("", response_model=TaskRead, status_code=201)
def add_task(payload: TaskCreate, db: Session = Depends(get_db)):
    return serialize_task(create_task(db, payload))

@router.patch("/{task_id}", response_model=TaskRead)
def patch_task(task_id: str, payload: TaskUpdate, db: Session = Depends(get_db)):
    try:
        return serialize_task(update_task(db, task_id, payload))
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.delete("/{task_id}", status_code=204)
def remove_task(task_id: str, db: Session = Depends(get_db)):
    try:
        delete_task(db, task_id)
        return Response(status_code=204)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
