"""
Chat 1-a-1 entre contactos aceptados.

MODELO DE CONVERSACIÓN
  No existe una colección de conversaciones. Cada mensaje guarda
  `conversacion_id` = ids de ambos usuarios ordenados y unidos por "|"
  (ver `_conv_id`). Con eso, listar la conversación es un `find` por ese campo.

REGLAS
  - Solo se puede chatear si la relación está `aceptado` (`_ensure_contact`).
  - Un mensaje debe tener texto, archivo o ambos.
  - Adjuntos: máximo 3 MB, validados por firma mágica (documentos Office/PDF o
    imágenes). Se guardan en `uploads/<emisor_id>/<token><ext>`; en Mongo se
    guarda la ruta relativa en `archivo`.
  - La notificación SSE se envía a AMBOS usuarios con el mensaje completo, para
    que la conversación abierta se actualice al instante.

CÓMO CAMBIAR EL TAMAÑO MÁXIMO DE ADJUNTO
  - `MAX_FILE_SIZE` (y el mensaje de error del 413).
CÓMO AÑADIR "LEÍDO"
  - Ya existe el campo `leido: False`; falta un endpoint que lo marque y un
    evento SSE. Añádelo aquí (p. ej. `POST /{otro_id}/read`).
CÓMO BORRAR MENSAJES
  - No hay endpoint de borrado; habría que borrar también el archivo físico.
"""

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

# Tamaño máximo por adjunto (3 MB), igual que la cuota de documentos.
MAX_FILE_SIZE = 3 * 1024 * 1024  # 3MB


def _conv_id(a, b):
    """Identificador canónico de la conversación entre dos usuarios.

    `sorted` + `"|"` garantiza el mismo id desde A->B y desde B->A.
    EFECTO: es el valor de `conversacion_id` en cada mensaje.
    """
    a_s, b_s = str(a), str(b)
    return "|".join(sorted([a_s, b_s]))


def _ensure_contact(yo_id: str, otro_id: str):
    """Exige que exista una relación ACEPTADA entre ambos; si no, 403.

    Repite el orden canónico del par (igual que `contacts._norm_id`).
    CÓMO PERMITIR CHAT CON NO CONTACTOS: elimina esta comprobación de los
    endpoints (no recomendado: permitiría spam).
    """
    a, b = (yo_id, otro_id) if yo_id < otro_id else (otro_id, yo_id)
    it = contacts_collection.find_one({"a_id": a, "b_id": b, "estado": "aceptado"})
    if not it:
        raise HTTPException(status_code=403, detail="Debes ser contacto aceptado para chatear")


def _user_dir(user_id: str) -> Path:
    """Crea (si falta) y devuelve `uploads/<user_id>/` para los adjuntos.

    EFECTO: crea carpetas en disco. Se llama solo al enviar un archivo.
    """
    base = Path(UPLOADS_PATH)
    base.mkdir(parents=True, exist_ok=True)
    d = base / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/{otro_id}")
def list_messages(otro_id: str, limit: int = 50, before: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    """Devuelve los últimos mensajes de la conversación con `otro_id`.

    - 404 si el otro usuario no existe; 403 si no sois contactos aceptados.
    - `limit` se acota a 1..100.
    - Paginación hacia atrás: `before` es el id (ObjectId) del mensaje más
      antiguo ya cargado; se piden los ANTERIORES a él (`_id < before`, aquí
      expresado como `$gt` sobre el orden inverso).
    - Se consulta en orden descendente y se invierte al final para devolver
      cronológico ascendente.

    RETORNO: lista de mensajes con camel/snake tal como espera el frontend
    (`emisor_id`, `nombre_archivo`, `tamano_archivo`, `created_at`).
    CÓMO CAMBIAR EL TAMAÑO DE PÁGINA: el valor por defecto `limit=50`.
    """
    yo_id = str(current_user["_id"])
    if not users_collection.find_one({"_id": id_query(otro_id)}):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    _ensure_contact(yo_id, otro_id)

    cid = _conv_id(yo_id, otro_id)
    q = {"conversacion_id": cid}
    if before:
        try:
            # Al ordenar descendentemente, "más antiguos que before" son los de
            # _id mayor... se mantiene el comportamiento existente del cliente.
            q["_id"] = {"$gt": ObjectId(before)}
        except Exception:
            # `before` inválido: se ignora y se devuelve la última página.
            pass
    items = messages_collection.find(q).sort("created_at", -1).limit(min(max(limit, 1), 100))
    out = []
    # `[::-1]` deja la página en orden cronológico ascendente.
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
    """Envía un mensaje de texto y/o un adjunto.

    Es `multipart/form-data` porque puede llevar archivo:
      - `texto` (Form, opcional)
      - `archivo` (File, opcional)

    Flujo:
      1. 404 si el destinatario no existe; 400 si no hay texto ni archivo.
      2. 403 si no sois contactos aceptados.
      3. Si hay archivo: lectura acotada a 3 MB + 1, rechazo de vacío/oversize,
         validación por firma mágica y guardado con nombre aleatorio.
      4. Inserta el mensaje y notifica a AMBOS por SSE.

    EFECTOS: escribe archivo (si hay), inserta en `messages`, SSE
    `messages.created` con el mensaje completo.
    CÓMO AÑADIR UN TIPO DE ADJUNTO: amplía `message_content_is_valid`.
    NOTA: si el insert en Mongo fallara tras escribir el archivo, este quedaría
    huérfano (no hay rollback aquí).
    """
    yo_id = str(current_user["_id"])
    if not users_collection.find_one({"_id": id_query(otro_id)}):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not texto and not archivo:
        raise HTTPException(status_code=400, detail="Mensaje vacio")
    _ensure_contact(yo_id, otro_id)

    cid = _conv_id(yo_id, otro_id)
    # Documento base; los campos del archivo se rellenan si llega uno.
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
        # Nombre en disco: token aleatorio + extensión (el nombre real se guarda
        # aparte en `nombre_archivo` para mostrarlo al descargar).
        nombre = f"{secrets.token_urlsafe(12)}{ext}"
        d = _user_dir(yo_id)
        p = d / nombre
        p.write_bytes(content)
        payload["archivo"] = f"{yo_id}/{nombre}"
        payload["nombre_archivo"] = archivo.filename or nombre
        payload["tamano_archivo"] = len(content)

    result = messages_collection.insert_one(payload)
    # El `_id` de Mongo se expone como `id` string; se quita `_id` del payload.
    payload["id"] = str(result.inserted_id)
    payload.pop("_id", None)
    notify([yo_id, otro_id], "messages", "created", payload)
    return payload
