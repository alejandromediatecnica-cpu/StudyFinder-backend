"""
Eventos de calendario: CRUD.

Un evento puede vincularse a una tarea mediante `task_id`. El vínculo lo crea el
frontend: al guardar una tarea con fecha, crea/actualiza un evento con `taskId`.
El backend solo persiste la referencia (no valida que la tarea exista).

CAMPOS ESPECIALES
  - `taskId` (alias de `task_id`): `populate_by_name=True` permite aceptar
    `taskId` del JSON y también construir el modelo por nombre de campo.
    OJO: `update_event` usa `model_dump(exclude_unset=True)` SIN `by_alias`, por
    eso las claves salen en snake_case (`task_id`) y encajan con la BD.
  - `date` es un string `YYYY-MM-DD` y `hour` un string `HH:MM` (no datetime):
    así se evitan líos de zona horaria entre navegador y servidor.

CÓMO AÑADIR UN CAMPO AL EVENTO
  - Añádelo a `EventCreate`/`EventUpdate`, guárdalo en `create_event`, mapéalo
    en `public_event` (y en `update_event` si necesita normalización).
CÓMO ORDENAR DISTINTO
  - `.sort("date", 1)` en `list_events` (hoy ascendente por fecha).
"""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.database import calendar_events_collection
from app.deps import get_current_user
from app.realtime import notify

router = APIRouter(prefix="/calendar", tags=["calendar"])


# ---------------------------------------------------------------------------
# ESQUEMAS
# ---------------------------------------------------------------------------
class EventCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(min_length=1, max_length=200)
    date: str = Field(min_length=1, max_length=10)
    hour: str | None = Field(default=None, max_length=5)
    type: str | None = Field(default="evento", max_length=20)
    subject: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    documents: list[dict] | None = None
    # `alias="taskId"`: el JSON puede enviar taskId; en Python es `task_id`.
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


# ---------------------------------------------------------------------------
# SERIALIZACIÓN
# ---------------------------------------------------------------------------
def public_event(event: dict) -> dict:
    """Documento de Mongo -> JSON camelCase (`taskId`, `createdAt`, `updatedAt`)."""
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


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------
@router.get("")
def list_events(current_user: dict = Depends(get_current_user)):
    """Lista los eventos del usuario ordenados por fecha ascendente.

    El frontend agrupa por día para pintar el calendario mensual.
    """
    user_id = str(current_user["_id"])
    events = calendar_events_collection.find({"user_id": user_id}).sort("date", 1)
    return [public_event(event) for event in events]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, current_user: dict = Depends(get_current_user)):
    """Crea un evento.

    - `title` se recorta; vacío -> 422.
    - `type` por defecto "evento" (el frontend usa tipos como "examen",
      "entrega", "estudio" para el color).
    EFECTOS: inserta en `calendar_events` y emite SSE `calendar.created`.
    """
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
    """Actualiza parcialmente un evento propio.

    - 404 si no existe o es de otro usuario.
    - `title` recortado y no vacío.
    - `changes` ya viene en snake_case (ver nota del módulo), así que se aplica
      tal cual con `$set`. `updated_at` se refresca solo si hay cambios.
    EFECTOS: escribe en `calendar_events` y emite `calendar.updated`.
    """
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
    """Borra un evento propio.

    NO borra la tarea vinculada: si el evento tenía `taskId`, el frontend decide
    si desvincularla (sigue existiendo en Tareas). Ver
    `DELETE /tasks` para el caso masivo.
    EFECTOS: borrado en `calendar_events`; SSE `calendar.deleted` con `{id}`.
    """
    user_id = str(current_user["_id"])
    event = calendar_events_collection.find_one({"id": event_id, "user_id": user_id})
    if not event:
        raise HTTPException(status_code=404, detail="No se encontró el evento.")
    calendar_events_collection.delete_one({"id": event_id, "user_id": user_id})
    notify([user_id], "calendar", "deleted", {"id": event_id})
