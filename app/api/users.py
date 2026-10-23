"""
Perfil del usuario: consulta, edición de nombre/idioma, avatar y baja de cuenta.

Este router usa `Depends(get_current_user)` (cabecera Bearer) en todos los
endpoints salvo la comprobación interna de rutas públicas. Las respuestas están
en camelCase (`hasAvatar`) porque el frontend (Angular) lo espera así.

CÓMO AÑADIR UN CAMPO EDITABLE AL PERFIL
  - Añádelo a `ProfileUpdate`, guárdalo en `changes` dentro de `update_me` y
    expónlo en `profile`. Actualiza `AuthUser`/`ProfileService` en el frontend.
CÓMO CAMBIAR EL LÍMITE DEL AVATAR
  - `MAX_AVATAR_SIZE` (2 MB) y el mensaje en `AVATAR_ERRORS`.
"""

import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import app.database as database
from app.api.auth import verify_password
from app.database import (
    normalize_name_key,
    revoke_user_sessions,
    users_collection,
)
from app.deps import get_current_user
from app.files import image_extension
from app.rate_limit import check_rate_limit
from app.realtime import notify

# Todas las rutas cuelgan de `/users`.
router = APIRouter(prefix="/users", tags=["users"])

# 2 MB de imagen de perfil, guardada dentro de `uploads/avatars/`.
MAX_AVATAR_SIZE = 2 * 1024 * 1024
AVATAR_DIR = "avatars"

# Mensajes de error reutilizados (uno por caso de fallo del avatar).
AVATAR_ERRORS = {
    "type": "La foto debe ser una imagen JPG, PNG o WebP.",
    "empty": "La imagen está vacía.",
    "size": "La foto no debe superar 2 MB.",
}


# ---------------------------------------------------------------------------
# ESQUEMAS
# ---------------------------------------------------------------------------
class ProfileUpdate(BaseModel):
    # Ambos opcionales: `PATCH /users/me` actualiza solo lo que llegue.
    name: str | None = Field(default=None, min_length=1, max_length=100)
    language: Literal["es", "en"] | None = None


class DeleteAccountRequest(BaseModel):
    # La baja exige la contraseña actual como confirmación.
    password: str = Field(min_length=1, max_length=128)


# ---------------------------------------------------------------------------
# SERIALIZACIÓN
# ---------------------------------------------------------------------------
def profile(user: dict) -> dict:
    """Representación del perfil propio (no expone hash ni ruta del avatar).

    `hasAvatar` es un booleano: el frontend pide la imagen a
    `GET /users/{id}/avatar` si es true.
    CÓMO AÑADIR UN CAMPO: añádelo aquí y en el modelo del frontend.
    """
    return {
        "id": str(user["_id"]),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "language": user.get("language") or "es",
        "hasAvatar": bool(user.get("avatar_path")),
    }


def _public_user(user: dict) -> dict:
    """Versión reducida para búsquedas/contactos (sin idioma)."""
    return {
        "id": str(user["_id"]),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "hasAvatar": bool(user.get("avatar_path")),
    }


# ---------------------------------------------------------------------------
# ARCHIVOS
# ---------------------------------------------------------------------------
def _uploads_root() -> Path:
    """Raíz de subidas leída en el momento de la llamada.

    Se lee de `database.UPLOADS_PATH` (y no de `config`) para que los tests
    puedan parchearla sin reimportar módulos.
    """
    return Path(database.UPLOADS_PATH)


def _remove_relative(relative: str | None) -> None:
    """Borra un archivo relativo a `uploads/`, con protección anti path-traversal.

    Resuelve la ruta y comprueba que siga dentro de la raíz antes de borrar.
    `missing_ok=True` hace la operación idempotente.
    EFECTO: borra del disco; no toca la base de datos.
    """
    if not relative:
        return
    root = _uploads_root().resolve()
    path = (root / relative).resolve()
    if path.is_relative_to(root):
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------
@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    """Devuelve el perfil del usuario autenticado."""
    return profile(current_user)


@router.patch("/me")
def update_me(payload: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    """Actualiza nombre y/o idioma.

    - Nombre: colapsa espacios, exige que no quede vacío y que su `name_key` no
      esté usado por otra cuenta (409). `$ne` al propio `_id` permite guardar el
      mismo nombre sin conflicto.
    - Idioma: "es" o "en" (validado por `Literal` en el esquema).
    EFECTOS: escribe en `users` solo si hay cambios y emite SSE `profile.updated`
    al propio usuario (para sincronizar otras pestañas).
    CÓMO CAMBIAR LA VALIDACIÓN DEL NOMBRE: edítala aquí.
    """
    changes: dict = {}

    if payload.name is not None:
        # "  Juan   Pérez " -> "Juan Pérez".
        name = " ".join(payload.name.split())
        if not name:
            raise HTTPException(status_code=422, detail="El nombre no puede estar vacío.")
        name_key = normalize_name_key(name)
        conflict = users_collection.find_one(
            {"name_key": name_key, "_id": {"$ne": current_user["_id"]}}
        )
        if conflict:
            raise HTTPException(
                status_code=409,
                detail="Ese nombre de usuario ya está en uso.",
            )
        changes["name"] = name
        changes["name_key"] = name_key

    if payload.language is not None:
        changes["language"] = payload.language

    if changes:
        users_collection.update_one({"_id": current_user["_id"]}, {"$set": changes})
        # Se refresca en memoria para devolver ya los valores nuevos.
        current_user = {**current_user, **changes}
        notify([str(current_user["_id"])], "profile", "updated", profile(current_user))
    return profile(current_user)


@router.put("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Sube (o reemplaza) la foto de perfil.

    Flujo:
      1. Lee como máximo `MAX_AVATAR_SIZE + 1` bytes (lectura acotada).
      2. Rechaza vacío (400), exceso (413) y formatos no reconocidos (400).
      3. Detecta la extensión REAL por firma mágica (`image_extension`), nunca
         por el nombre del archivo.
      4. Guarda en `uploads/avatars/{user_id}{ext}` y borra la anterior si
         cambiaba de extensión.
      5. Actualiza `avatar_path` y emite SSE.

    EFECTOS: escribe/borra archivos y actualiza `users`.
    CÓMO ACEPTAR OTRO FORMATO: añádelo a `IMAGE_MAGIC` en `app/files.py`.
    CÓMO CAMBIAR EL NOMBRE DEL ARCHIVO: `relative_path`.
    """
    content = await file.read(MAX_AVATAR_SIZE + 1)
    await file.close()

    if not content:
        raise HTTPException(status_code=400, detail=AVATAR_ERRORS["empty"])
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=413, detail=AVATAR_ERRORS["size"])
    extension = image_extension(content)
    if not extension:
        raise HTTPException(status_code=400, detail=AVATAR_ERRORS["type"])

    # Nombre estable por usuario: la extensión cambia según el formato subido.
    previous = current_user.get("avatar_path")
    relative_path = f"{AVATAR_DIR}/{current_user['_id']}{extension}"
    destination = _uploads_root() / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    # Solo se borra si la ruta anterior era distinta (evita borrar la recién escrita).
    if previous and previous != relative_path:
        _remove_relative(previous)

    users_collection.update_one(
        {"_id": current_user["_id"]}, {"$set": {"avatar_path": relative_path}}
    )
    updated = {**current_user, "avatar_path": relative_path}
    notify([str(current_user["_id"])], "profile", "updated", profile(updated))
    return {"hasAvatar": True}


@router.delete("/me/avatar", status_code=status.HTTP_204_NO_CONTENT)
def delete_avatar(current_user: dict = Depends(get_current_user)):
    """Elimina la foto de perfil (archivo y referencia en BD).

    EFECTOS: borra el archivo, pone `avatar_path=None` y emite SSE.
    """
    _remove_relative(current_user.get("avatar_path"))
    users_collection.update_one(
        {"_id": current_user["_id"]}, {"$set": {"avatar_path": None}}
    )
    notify([str(current_user["_id"])], "profile", "updated", profile(
        {**current_user, "avatar_path": None}
    ))


@router.get("/{user_id}/avatar")
def get_avatar(user_id: str, current_user: dict = Depends(get_current_user)):
    """Devuelve la imagen de perfil de cualquier usuario autenticado.

    - `id_query` normaliza ids legacy/ObjectId.
    - 404 si el usuario no existe o no tiene avatar.
    - Comprueba path-traversal e inexistencia del archivo antes de servirlo.
    - `Cache-Control: private, max-age=60` permite cachear 1 min en el navegador
      (suficiente para que un cambio de avatar se vea pronto).
    EFECTO: lectura de disco. `current_user` solo sirve para exigir sesión.
    """
    user = users_collection.find_one({"_id": database.id_query(user_id)})
    if not user or not user.get("avatar_path"):
        raise HTTPException(status_code=404, detail="Este usuario no tiene foto de perfil.")
    root = _uploads_root().resolve()
    path = (root / user["avatar_path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="La foto ya no está disponible.")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    payload: DeleteAccountRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Baja definitiva de la cuenta (irreversible).

    Flujo:
      1. Rate limit 5 intentos / 5 min por usuario+IP.
      2. Confirma la contraseña (400 si no coincide).
      3. Antes de borrar, recoge los contactos ACEPTADOS para avisarles por SSE.
      4. `delete_user_data` borra en cascada en Mongo y devuelve lo necesario
         para limpiar disco (documentos y adjuntos).
      5. Borra archivos: documentos, adjuntos de mensajes y avatar.
      6. Revoca sesiones y emite `contacts.updated` a los contactos.

    EFECTOS: borrados en 9 colecciones, borrados en disco y eventos SSE.
    NOTA: los mensajes recibidos de otros NO se borran (son de sus autores), pero
    los contactos verán que la relación desaparece.
    CÓMO PEDIR MÁS CONFIRMACIÓN: amplía `DeleteAccountRequest`.
    """
    if not check_rate_limit(
        f"delete-account:{current_user['_id']}:{request.client.host if request.client else 'local'}",
        5,
        300,
    ):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
        )
    if not verify_password(current_user, payload.password):
        raise HTTPException(status_code=400, detail="La contraseña no es correcta.")

    user_id = str(current_user["_id"])
    # Contraparte de cada contacto aceptado (para avisarles después del borrado).
    contact_ids = [
        contact.get("b_id") if contact.get("a_id") == user_id else contact.get("a_id")
        for contact in database.contacts_collection.find(
            {"estado": "aceptado", "$or": [{"a_id": user_id}, {"b_id": user_id}]}
        )
    ]
    avatar_path = current_user.get("avatar_path")
    _, documents, attachments = database.delete_user_data(user_id)

    # Limpieza física: documentos, adjuntos y avatar.
    for document in documents:
        _remove_relative(document.get("storage_path"))
    for attachment in attachments:
        _remove_relative(attachment)
    _remove_relative(avatar_path)
    revoke_user_sessions(user_id)
    # Los contactos recargan su lista (ya no aparecerá este usuario).
    notify([contact_id for contact_id in contact_ids if contact_id], "contacts", "updated", {})
    return None


@router.get("/search")
def search_users(nombre: str, current_user: dict = Depends(get_current_user)):
    """Busca usuarios por nombre (parcial, insensible a mayúsculas) para
    agregarlos como contactos. Excluye al propio usuario.

    - Término mínimo: 2 caracteres (si no, devuelve []).
    - `re.escape` evita que el usuario inyecte regex.
    - Límite de 20 resultados.
    CÓMO CAMBIAR EL LÍMITE: `.limit(20)`.
    CÓMO BUSCAR TAMBIÉN POR EMAIL: añade un `$or` al filtro.
    """
    termino = (nombre or "").strip()
    if len(termino) < 2:
        return []
    filtro = {
        "name": {"$regex": re.escape(termino), "$options": "i"},
        "_id": {"$ne": current_user["_id"]},
    }
    return [_public_user(user) for user in users_collection.find(filtro).limit(20)]
