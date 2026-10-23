"""
Tareas: CRUD y borrado masivo.

SINCRONIZACIÓN CON EL CALENDARIO (lado frontend)
  El backend trata tareas y eventos como colecciones independientes. Cuando una
  tarea tiene `dueDate`, el frontend crea/actualiza un evento con `taskId` para
  que aparezca en el calendario (ver `TaskStoreService` y
  `CalendarStoreService` en el frontend). Por eso `DELETE /tasks` (masivo) borra
  TAMBIÉN los eventos, para no dejar huérfanos.

CONVENCIÓN DE CAMPOS
  La API habla camelCase (`dueDate`, `dueTime`) y la BD snake_case (`due_date`,
  `due_time`). `_changes` y `public_task` hacen la traducción.

CÓMO AÑADIR UN CAMPO A LA TAREA
  - Añádelo a `TaskCreate`/`TaskUpdate`, mapéalo en `_changes`, expónlo en
    `public_task`, guárdalo en `create_task` y actualiza el modelo del frontend.
CÓMO CAMBIAR LOS ESTADOS VÁLIDOS
  - Hoy `status` es texto libre (máx. 20). Si quieres validarlo, cámbialo a
    `Literal[...]` en los esquemas y ajusta el frontend.
"""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import calendar_events_collection, tasks_collection
from app.deps import get_current_user
from app.realtime import notify

# Todas las rutas cuelgan de `/tasks` y requieren sesión.
router = APIRouter(prefix="/tasks", tags=["tasks"])


# ---------------------------------------------------------------------------
# ESQUEMAS
# ---------------------------------------------------------------------------
class TaskCreate(BaseModel):
    # `documents` es una lista libre de dicts (los adjunta el frontend con
    # id/nombre); el backend no valida su forma.
    title: str = Field(min_length=1, max_length=200)
    subject: str | None = Field(default=None, max_length=120)
    dueDate: str | None = Field(default=None, max_length=10)
    dueTime: str | None = Field(default=None, max_length=5)
    status: str | None = Field(default="pending", max_length=20)
    type: str | None = Field(default="tarea", max_length=20)
    description: str | None = Field(default=None, max_length=2000)
    documents: list[dict] | None = None


class TaskUpdate(BaseModel):
    # Todo opcional: PATCH solo aplica las claves presentes (`exclude_unset`).
    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, max_length=120)
    dueDate: str | None = Field(default=None, max_length=10)
    dueTime: str | None = Field(default=None, max_length=5)
    status: str | None = Field(default=None, max_length=20)
    type: str | None = Field(default=None, max_length=20)
    completed: bool | None = None
    description: str | None = Field(default=None, max_length=2000)
    documents: list[dict] | None = None


# ---------------------------------------------------------------------------
# SERIALIZACIÓN Y MAPEO
# ---------------------------------------------------------------------------
def public_task(task: dict) -> dict:
    """Documento de Mongo -> JSON camelCase para el frontend.

    `completed` se deriva de `status` al crear y se mantiene al marcar/desmarcar.
    """
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
    """Traduce el payload camelCase a campos de MongoDB (snake_case).

    Solo incluye claves enviadas (`exclude_unset`), de modo que un PATCH nunca
    pisa campos no enviados. `title` se recorta.

    REGLA ESPECIAL: si llega `completed` sin `status`, se sincroniza
    `status` = "done"/"pending". Así marcar la casilla actualiza los dos campos.
    CÓMO AÑADIR UN CAMPO: un `if "<campo>" in data: changes["<columna>"] = ...`.
    """
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


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------
@router.get("")
def list_tasks(current_user: dict = Depends(get_current_user)):
    """Lista las tareas del usuario, más recientes primero."""
    user_id = str(current_user["_id"])
    tasks = tasks_collection.find({"user_id": user_id}).sort("created_at", -1)
    return [public_task(task) for task in tasks]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, current_user: dict = Depends(get_current_user)):
    """Crea una tarea.

    - `title` se recorta y no puede quedar vacío (422).
    - `completed` se inicializa según `status == "done"`.
    - `id` es un uuid4 hex (independiente del `_id` interno).
    EFECTOS: inserta en `tasks` y emite SSE `tasks.created` con la tarea entera.
    CÓMO CAMBIAR LAS NOTIFICACIONES: aquí y en `update_task`/`delete_task`.
    """
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
    # SSE tras persistir: el frontend recarga/muta su lista de tareas.
    notify([user_id], "tasks", "created", result)
    return result


@router.patch("/{task_id}")
def update_task(
    task_id: str,
    payload: TaskUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Actualiza parcialmente una tarea propia.

    - 404 si no existe o es de otro usuario.
    - Si llega `title`, se valida no vacío.
    - Solo escribe si hay cambios; siempre actualiza `updated_at`.
    - Relee el documento para devolver el estado real y notificar por SSE.
    EFECTOS: escribe en `tasks` y emite `tasks.updated`.
    """
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
    """Borra de una sola vez todas las tareas y eventos del usuario.

    Es el endpoint que usa el botón de "borrar todo" (doble confirmación en la
    UI). Borrar también los eventos evita que queden eventos de calendario sin
    tarea tras un borrado masivo.
    EFECTOS: `delete_many` en `tasks` y `calendar_events`; SSE `tasks.deleted`
    con payload vacío (el frontend vacía la lista).
    ADVERTENCIA: no se puede deshacer.
    """
    user_id = str(current_user["_id"])
    tasks_collection.delete_many({"user_id": user_id})
    calendar_events_collection.delete_many({"user_id": user_id})
    notify([user_id], "tasks", "deleted", {})


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    """Borra una tarea propia.

    NO borra el evento de calendario asociado: el frontend lo reconcilia/elimina
    (ver `reconciliarEventosSinTarea`). Si prefieres borrarlo también en el
    backend, añade aquí `calendar_events_collection.delete_many({"task_id": task_id, "user_id": user_id})`.
    EFECTOS: borrado en `tasks`; SSE `tasks.deleted` con `{id}`.
    """
    user_id = str(current_user["_id"])
    task = tasks_collection.find_one({"id": task_id, "user_id": user_id})
    if not task:
        raise HTTPException(status_code=404, detail="No se encontró la tarea.")
    tasks_collection.delete_one({"id": task_id, "user_id": user_id})
    notify([user_id], "tasks", "deleted", {"id": task_id})
