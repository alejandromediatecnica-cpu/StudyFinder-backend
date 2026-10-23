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

router = APIRouter(prefix="/users", tags=["users"])

MAX_AVATAR_SIZE = 2 * 1024 * 1024
AVATAR_DIR = "avatars"

AVATAR_ERRORS = {
    "type": "La foto debe ser una imagen JPG, PNG o WebP.",
    "empty": "La imagen está vacía.",
    "size": "La foto no debe superar 2 MB.",
}


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    language: Literal["es", "en"] | None = None


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


def profile(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "language": user.get("language") or "es",
        "hasAvatar": bool(user.get("avatar_path")),
    }


def _public_user(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "hasAvatar": bool(user.get("avatar_path")),
    }


def _uploads_root() -> Path:
    return Path(database.UPLOADS_PATH)


def _remove_relative(relative: str | None) -> None:
    if not relative:
        return
    root = _uploads_root().resolve()
    path = (root / relative).resolve()
    if path.is_relative_to(root):
        path.unlink(missing_ok=True)


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return profile(current_user)


@router.patch("/me")
def update_me(payload: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    changes: dict = {}
    if payload.name is not None:
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
        current_user = {**current_user, **changes}
        notify([str(current_user["_id"])], "profile", "updated", profile(current_user))
    return profile(current_user)


@router.put("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    content = await file.read(MAX_AVATAR_SIZE + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=400, detail=AVATAR_ERRORS["empty"])
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=413, detail=AVATAR_ERRORS["size"])
    extension = image_extension(content)
    if not extension:
        raise HTTPException(status_code=400, detail=AVATAR_ERRORS["type"])

    previous = current_user.get("avatar_path")
    relative_path = f"{AVATAR_DIR}/{current_user['_id']}{extension}"
    destination = _uploads_root() / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
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
    _remove_relative(current_user.get("avatar_path"))
    users_collection.update_one(
        {"_id": current_user["_id"]}, {"$set": {"avatar_path": None}}
    )
    notify([str(current_user["_id"])], "profile", "updated", profile(
        {**current_user, "avatar_path": None}
    ))


@router.get("/{user_id}/avatar")
def get_avatar(user_id: str, current_user: dict = Depends(get_current_user)):
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
    contact_ids = [
        contact.get("b_id") if contact.get("a_id") == user_id else contact.get("a_id")
        for contact in database.contacts_collection.find(
            {"estado": "aceptado", "$or": [{"a_id": user_id}, {"b_id": user_id}]}
        )
    ]
    avatar_path = current_user.get("avatar_path")
    _, documents, attachments = database.delete_user_data(user_id)

    for document in documents:
        _remove_relative(document.get("storage_path"))
    for attachment in attachments:
        _remove_relative(attachment)
    _remove_relative(avatar_path)
    revoke_user_sessions(user_id)
    notify([contact_id for contact_id in contact_ids if contact_id], "contacts", "updated", {})
    return None


@router.get("/search")
def search_users(nombre: str, current_user: dict = Depends(get_current_user)):
    """Busca usuarios por nombre (parcial, insensible a mayúsculas) para
    agregarlos como contactos. Excluye al propio usuario."""
    termino = (nombre or "").strip()
    if len(termino) < 2:
        return []
    filtro = {
        "name": {"$regex": re.escape(termino), "$options": "i"},
        "_id": {"$ne": current_user["_id"]},
    }
    return [_public_user(user) for user in users_collection.find(filtro).limit(20)]
