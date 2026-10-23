import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from bson import ObjectId

from app.database import contacts_collection, id_query, messages_collection, users_collection, UPLOADS_PATH
from app.deps import get_current_user
from app.files import message_content_is_valid
from app.realtime import notify

router = APIRouter(prefix="/messages", tags=["messages"])

MAX_FILE_SIZE = 3 * 1024 * 1024  # 3MB


def _conv_id(a, b):
    a_s, b_s = str(a), str(b)
    return "|".join(sorted([a_s, b_s]))


def _ensure_contact(yo_id: str, otro_id: str):
    a, b = (yo_id, otro_id) if yo_id < otro_id else (otro_id, yo_id)
    it = contacts_collection.find_one({"a_id": a, "b_id": b, "estado": "aceptado"})
    if not it:
        raise HTTPException(status_code=403, detail="Debes ser contacto aceptado para chatear")


def _user_dir(user_id: str) -> Path:
    base = Path(UPLOADS_PATH)
    base.mkdir(parents=True, exist_ok=True)
    d = base / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/{otro_id}")
def list_messages(otro_id: str, limit: int = 50, before: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    yo_id = str(current_user["_id"])
    if not users_collection.find_one({"_id": id_query(otro_id)}):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    _ensure_contact(yo_id, otro_id)
    cid = _conv_id(yo_id, otro_id)
    q = {"conversacion_id": cid}
    if before:
        try:
            q["_id"] = {"$gt": ObjectId(before)}
        except Exception:
            pass
    items = messages_collection.find(q).sort("created_at", -1).limit(min(max(limit, 1), 100))
    out = []
    for it in list(items)[::-1]:
        out.append({
            "id": str(it["_id"]),
            "emisor_id": it.get("emisor_id"),
            "receptor_id": it.get("receptor_id"),
            "texto": it.get("texto"),
            "archivo": it.get("archivo"),
            "nombre_archivo": it.get("nombre_archivo"),
            "tamano_archivo": it.get("tamano_archivo"),
            "created_at": it.get("created_at"),
        })
    return out


@router.post("/{otro_id}", status_code=status.HTTP_201_CREATED)
async def send_message(otro_id: str, texto: str = Form(""), archivo: UploadFile | None = File(None), current_user: dict = Depends(get_current_user)):
    yo_id = str(current_user["_id"])
    if not users_collection.find_one({"_id": id_query(otro_id)}):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not texto and not archivo:
        raise HTTPException(status_code=400, detail="Mensaje vacio")
    _ensure_contact(yo_id, otro_id)
    cid = _conv_id(yo_id, otro_id)
    payload = {
        "conversacion_id": cid,
        "emisor_id": yo_id,
        "receptor_id": otro_id,
        "texto": texto or None,
        "archivo": None,
        "nombre_archivo": None,
        "tamano_archivo": None,
        "created_at": datetime.now(timezone.utc),
        "leido": False,
    }
    if archivo:
        content = await archivo.read(MAX_FILE_SIZE + 1)
        await archivo.close()
        if not content:
            raise HTTPException(status_code=400, detail="El archivo está vacío")
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="El archivo no debe superar 3 MB")
        ext = Path(archivo.filename or "").suffix.lower()
        if not message_content_is_valid(ext, content):
            raise HTTPException(
                status_code=400,
                detail="Solo puedes adjuntar documentos PDF, Office o imágenes válidas.",
            )
        nombre = f"{secrets.token_urlsafe(12)}{ext}"
        d = _user_dir(yo_id)
        p = d / nombre
        p.write_bytes(content)
        payload["archivo"] = f"{yo_id}/{nombre}"
        payload["nombre_archivo"] = archivo.filename or nombre
        payload["tamano_archivo"] = len(content)
    result = messages_collection.insert_one(payload)
    payload["id"] = str(result.inserted_id)
    payload.pop("_id", None)
    notify([yo_id, otro_id], "messages", "created", payload)
    return payload
