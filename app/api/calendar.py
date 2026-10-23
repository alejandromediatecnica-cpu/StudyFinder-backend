from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.database import calendar_events_collection
from app.deps import get_current_user
from app.realtime import notify

router = APIRouter(prefix="/calendar", tags=["calendar"])


class EventCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(min_length=1, max_length=200)
    date: str = Field(min_length=1, max_length=10)
    hour: str | None = Field(default=None, max_length=5)
    type: str | None = Field(default="evento", max_length=20)
    subject: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    documents: list[dict] | None = None
    task_id: str | None = Field(default=None, max_length=64, alias="taskId")


class EventUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    date: str | None = Field(default=None, min_length=1, max_length=10)
    hour: str | None = Field(default=None, max_length=5)
    type: str | None = Field(default=None, max_length=20)
    subject: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    documents: list[dict] | None = None
    task_id: str | None = Field(default=None, max_length=64, alias="taskId")


def public_event(event: dict) -> dict:
    return {
        "id": event["id"],
        "title": event["title"],
        "date": event["date"],
        "hour": event.get("hour"),
        "type": event.get("type", "evento"),
        "subject": event.get("subject"),
        "description": event.get("description"),
        "documents": event.get("documents") or [],
        "taskId": event.get("task_id"),
        "createdAt": event.get("created_at"),
        "updatedAt": event.get("updated_at"),
    }


@router.get("")
def list_events(current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    events = calendar_events_collection.find({"user_id": user_id}).sort("date", 1)
    return [public_event(event) for event in events]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="El título es obligatorio.")
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "id": uuid4().hex,
        "user_id": user_id,
        "title": title,
        "date": payload.date,
        "hour": payload.hour,
        "type": payload.type or "evento",
        "subject": payload.subject,
        "description": payload.description,
        "documents": payload.documents or [],
        "task_id": payload.task_id,
        "created_at": now,
        "updated_at": now,
    }
    calendar_events_collection.insert_one(event)
    result = public_event(event)
    notify([user_id], "calendar", "created", result)
    return result


@router.patch("/{event_id}")
def update_event(
    event_id: str,
    payload: EventUpdate,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user["_id"])
    event = calendar_events_collection.find_one({"id": event_id, "user_id": user_id})
    if not event:
        raise HTTPException(status_code=404, detail="No se encontró el evento.")
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        changes["title"] = changes["title"].strip()
        if not changes["title"]:
            raise HTTPException(status_code=422, detail="El título es obligatorio.")
    if changes:
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        calendar_events_collection.update_one(
            {"id": event_id, "user_id": user_id}, {"$set": changes}
        )
    updated = calendar_events_collection.find_one({"id": event_id, "user_id": user_id})
    result = public_event(updated)
    notify([user_id], "calendar", "updated", result)
    return result


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: str, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    event = calendar_events_collection.find_one({"id": event_id, "user_id": user_id})
    if not event:
        raise HTTPException(status_code=404, detail="No se encontró el evento.")
    calendar_events_collection.delete_one({"id": event_id, "user_id": user_id})
    notify([user_id], "calendar", "deleted", {"id": event_id})
