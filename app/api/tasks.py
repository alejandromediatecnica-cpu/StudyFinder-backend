from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import calendar_events_collection, tasks_collection
from app.deps import get_current_user
from app.realtime import notify

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    subject: str | None = Field(default=None, max_length=120)
    dueDate: str | None = Field(default=None, max_length=10)
    dueTime: str | None = Field(default=None, max_length=5)
    status: str | None = Field(default="pending", max_length=20)
    type: str | None = Field(default="tarea", max_length=20)
    description: str | None = Field(default=None, max_length=2000)
    documents: list[dict] | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, max_length=120)
    dueDate: str | None = Field(default=None, max_length=10)
    dueTime: str | None = Field(default=None, max_length=5)
    status: str | None = Field(default=None, max_length=20)
    type: str | None = Field(default=None, max_length=20)
    completed: bool | None = None
    description: str | None = Field(default=None, max_length=2000)
    documents: list[dict] | None = None


def public_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "title": task["title"],
        "subject": task.get("subject"),
        "dueDate": task.get("due_date"),
        "dueTime": task.get("due_time"),
        "status": task.get("status", "pending"),
        "type": task.get("type", "tarea"),
        "completed": bool(task.get("completed", False)),
        "description": task.get("description"),
        "documents": task.get("documents") or [],
        "createdAt": task.get("created_at"),
        "updatedAt": task.get("updated_at"),
    }


def _changes(payload: TaskCreate | TaskUpdate) -> dict:
    data = payload.model_dump(exclude_unset=True)
    changes: dict = {}
    if "title" in data:
        changes["title"] = data["title"].strip()
    if "subject" in data:
        changes["subject"] = data["subject"]
    if "dueDate" in data:
        changes["due_date"] = data["dueDate"]
    if "dueTime" in data:
        changes["due_time"] = data["dueTime"]
    if "status" in data:
        changes["status"] = data["status"]
    if "type" in data:
        changes["type"] = data["type"]
    if "description" in data:
        changes["description"] = data["description"]
    if "documents" in data:
        changes["documents"] = data["documents"] or []
    if "completed" in data:
        changes["completed"] = bool(data["completed"])
        if "status" not in data:
            changes["status"] = "done" if data["completed"] else "pending"
    return changes


@router.get("")
def list_tasks(current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    tasks = tasks_collection.find({"user_id": user_id}).sort("created_at", -1)
    return [public_task(task) for task in tasks]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="El título es obligatorio.")
    now = datetime.now(timezone.utc).isoformat()
    task = {
        "id": uuid4().hex,
        "user_id": user_id,
        "title": title,
        "subject": payload.subject,
        "due_date": payload.dueDate,
        "due_time": payload.dueTime,
        "status": payload.status or "pending",
        "type": payload.type or "tarea",
        "completed": (payload.status == "done"),
        "description": payload.description,
        "documents": payload.documents or [],
        "created_at": now,
        "updated_at": now,
    }
    tasks_collection.insert_one(task)
    result = public_task(task)
    notify([user_id], "tasks", "created", result)
    return result


@router.patch("/{task_id}")
def update_task(
    task_id: str,
    payload: TaskUpdate,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user["_id"])
    task = tasks_collection.find_one({"id": task_id, "user_id": user_id})
    if not task:
        raise HTTPException(status_code=404, detail="No se encontró la tarea.")
    changes = _changes(payload)
    if "title" in changes and not changes["title"]:
        raise HTTPException(status_code=422, detail="El título es obligatorio.")
    if changes:
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        tasks_collection.update_one({"id": task_id, "user_id": user_id}, {"$set": changes})
    updated = tasks_collection.find_one({"id": task_id, "user_id": user_id})
    result = public_task(updated)
    notify([user_id], "tasks", "updated", result)
    return result


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_all_tasks(current_user: dict = Depends(get_current_user)):
    """Borra de una sola vez todas las tareas y eventos del usuario."""
    user_id = str(current_user["_id"])
    tasks_collection.delete_many({"user_id": user_id})
    calendar_events_collection.delete_many({"user_id": user_id})
    notify([user_id], "tasks", "deleted", {})


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    task = tasks_collection.find_one({"id": task_id, "user_id": user_id})
    if not task:
        raise HTTPException(status_code=404, detail="No se encontró la tarea.")
    tasks_collection.delete_one({"id": task_id, "user_id": user_id})
    notify([user_id], "tasks", "deleted", {"id": task_id})
