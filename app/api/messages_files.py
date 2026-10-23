"""
Descarga de adjuntos de chat.

Ruta pensada para el `src` de imágenes/descargas del frontend:
`GET /messages/files/<emisor_id>/<archivo>`.

SEGURIDAD
  - El primer segmento es el dueño del archivo. Si no soy yo, se exige ser
    contacto ACEPTADO con él (`_ensure_contact`). Así solo los contactos pueden
    descargar lo que se han enviado.
  - La ruta debe tener exactamente dos segmentos (`owner/archivo`); si no, 400.

CÓMO AÑADIR TIPOS MIME/CONTENT-DISPOSITION
  - Cambia `FileResponse(p)` por `FileResponse(p, media_type=..., filename=...)`.
CÓMO PERMITIR DESCARGAS A CUALQUIER AUTENTICADO
  - Quita la llamada a `_ensure_contact`.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from app.database import contacts_collection, messages_collection, UPLOADS_PATH
from app.deps import get_current_user

router = APIRouter(prefix="/messages/files", tags=["messages"])


def _ensure_contact(yo_id: str, otro_id: str):
    """Exige relación ACEPTADA con el dueño del archivo; si no, 403.

    Mensaje genérico ("No autorizado") para no revelar si existe la relación.
    """
    a, b = (yo_id, otro_id) if yo_id < otro_id else (otro_id, yo_id)
    it = contacts_collection.find_one({"a_id": a, "b_id": b, "estado": "aceptado"})
    if not it:
        raise HTTPException(status_code=403, detail="No autorizado")


@router.get("/{path:path}")
def download(path: str, current_user: dict = Depends(get_current_user)):
    """Sirve el archivo si el usuario tiene permiso.

    - `{path:path}` captura "owner/nombre.ext" completo.
    - 400 si no son exactamente 2 segmentos.
    - Autorización: es mío o es de un contacto aceptado.
    - 404 si no existe o no es un archivo.
    CÓMO IMPEDIR PATH TRAVERSAL: `Path(UPLOADS_PATH) / path` podría escapar con
    "../"; aquí se valida la forma (2 segmentos) pero no se resuelve la ruta.
    """
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
