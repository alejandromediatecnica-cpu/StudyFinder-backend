from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from app.database import contacts_collection, messages_collection, UPLOADS_PATH
from app.deps import get_current_user

router = APIRouter(prefix="/messages/files", tags=["messages"])


def _ensure_contact(yo_id: str, otro_id: str):
    a, b = (yo_id, otro_id) if yo_id < otro_id else (otro_id, yo_id)
    it = contacts_collection.find_one({"a_id": a, "b_id": b, "estado": "aceptado"})
    if not it:
        raise HTTPException(status_code=403, detail="No autorizado")


@router.get("/{path:path}")
def download(path: str, current_user: dict = Depends(get_current_user)):
    parts = path.split("/")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="Ruta invalida")
    owner = parts[0]
    yo_id = str(current_user["_id"])
    if owner != yo_id:
        _ensure_contact(yo_id, owner)
    p = Path(UPLOADS_PATH) / path
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(p)
