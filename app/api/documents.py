"""
Biblioteca de documentos: subida, cuota, descarga, asociación a materias y
borrado.

PUNTOS CLAVE
  - Cuota: 3 MB por usuario (`MAX_STORAGE_PER_USER`), y por tanto el máximo por
    archivo también es 3 MB. La cuota se comprueba y se escribe bajo un lock por
    usuario para evitar carreras (dos subidas simultáneas que juntas superen la
    cuota).
  - Validación doble: extensión permitida + contenido real por firma mágica
    (`app/files.py`). Un PDF renombrado a .docx se rechaza.
  - Los archivos viven en `uploads/<user_id>/<uuid><ext>`; en Mongo solo quedan
    metadatos. Todas las rutas se resuelven y se valida que no salgan de
    `uploads/` (path traversal).
  - Autenticación propia (`authenticated_user_id`) porque estos endpoints
    aceptan la cabecera opcionalmente para personalizar errores; el token se
    resuelve con `user_id_for_token`.

CÓMO CAMBIAR LA CUOTA
  - `MAX_STORAGE_PER_USER` (y `MAX_FILE_SIZE`, que lo deriva). Cambia también el
    mensaje de error y la vista de almacenamiento del frontend.
CÓMO PERMITIR OTRO FORMATO
  - Añádelo a `ALLOWED_EXTENSIONS` y su firma a `DOCUMENT_MAGIC` en `files.py`.
"""

import threading
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.database import (
    UPLOADS_PATH,
    associate_document,
    create_document,
    delete_document,
    get_document_by_id,
    list_documents,
    list_documents_from_others,
    remove_document_association,
    storage_info_for_user,
    total_storage_for_user,
    user_id_for_token,
)
from app.files import document_content_is_valid
from app.realtime import notify

router = APIRouter(prefix="/documents", tags=["documents"])

# Cuota total y tamaño máximo por archivo. `MAX_FILE_SIZE` se deriva de la cuota:
# un solo archivo nunca puede exceder el espacio total disponible.
MAX_STORAGE_PER_USER = 3 * 1024 * 1024
MAX_FILE_SIZE = MAX_STORAGE_PER_USER

# Extensiones aceptadas (en minúsculas, con punto).
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}

# Locks por usuario. `defaultdict(threading.Lock)` crea uno nuevo al primer uso;
# `_locks_guard` protege el dict frente a accesos concurrentes.
_locks_guard = threading.Lock()
_user_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _user_lock(user_id: str) -> threading.Lock:
    """Devuelve (creando si hace falta) el lock del usuario.

    EFECTO: serializa la comprobación de cuota + escritura de archivo + insert en
    Mongo para un mismo usuario.
    """
    with _locks_guard:
        return _user_locks[user_id]


class MateriaAssociation(BaseModel):
    # Nombre de la materia a la que se asocia el documento.
    materia: str = Field(min_length=1, max_length=120)


def authenticated_user_id(authorization: str | None) -> str:
    """Extrae y valida el `user_id` del Bearer token.

    A diferencia de `get_current_user`, no devuelve el documento completo: aquí
    solo hace falta el id. Lanza 401 si falta la cabecera o el token no existe.
    CÓMO REUTILIZAR ESTA AUTENTICACIÓN EN OTRO ROUTER: importa esta función o usa
    `Depends(get_current_user)` si necesitas el usuario entero.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    user_id = user_id_for_token(authorization.removeprefix("Bearer ").strip())
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión ya no es válida. Inicia sesión de nuevo.")
    return user_id


def public_document(document: dict) -> dict:
    """Convierte el documento de Mongo al JSON que consume el frontend.

    Traduce snake_case -> camelCase (`contentType`, `createdAt`) y oculta
    `storage_path`/`user_id`.
    CÓMO AÑADIR UN CAMPO: añádelo aquí y en el modelo `StudyDocument` del
    frontend.
    """
    return {
        "id": document["id"],
        "filename": document["filename"],
        "contentType": document["content_type"],
        "size": document["size"],
        "createdAt": document["created_at"],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    """Sube un documento a la biblioteca personal.

    Flujo:
      1. Autentica y sanea el nombre (`Path(...).name` evita rutas del cliente).
      2. Valida extensión permitida.
      3. Lee como máximo `MAX_FILE_SIZE + 1` bytes (lectura acotada) y valida
         vacío / tamaño / firma mágica.
      4. Bajo el lock del usuario: comprueba cuota, crea carpeta, escribe el
         archivo e inserta metadatos. Si el insert falla, borra el archivo
         (rollback manual).
      5. Emite SSE `documents.created` con el id.

    EFECTOS: escribe archivo + `documents`; SSE.
    IMPORTANTE: se notifica DESPUÉS de persistir; el evento solo lleva el id, y
    el frontend recarga la lista.
    CÓMO CAMBIAR EL NOMBRE EN DISCO: `storage_name`/`relative_path`.
    """
    user_id = authenticated_user_id(authorization)

    # 1) Nombre saneado: solo el último componente del nombre enviado.
    filename = Path(file.filename or "").name.strip()
    extension = Path(filename).suffix.lower()
    if not filename or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido.")

    # 3) Lectura acotada y validaciones de contenido.
    content = await file.read(MAX_FILE_SIZE + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="El tamaño máximo por archivo es 3 MB.")
    if not document_content_is_valid(extension, content):
        raise HTTPException(
            status_code=400,
            detail="El contenido del archivo no coincide con su extensión.",
        )

    # 4) Ruta destino: uploads/<user_id>/<uuid><ext>.
    storage_name = f"{uuid4().hex}{extension}"
    relative_path = Path(user_id) / storage_name
    destination = UPLOADS_PATH / relative_path

    with _user_lock(user_id):
        if total_storage_for_user(user_id) + len(content) > MAX_STORAGE_PER_USER:
            raise HTTPException(
                status_code=413,
                detail="No te queda espacio: cada usuario dispone de 3 MB de almacenamiento.",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        try:
            document = create_document(
                user_id=user_id,
                filename=filename,
                storage_path=relative_path.as_posix(),
                content_type=file.content_type or "application/octet-stream",
                size=len(content),
            )
        except Exception:
            # Rollback: si no se pudo registrar el metadato, el archivo sobra.
            destination.unlink(missing_ok=True)
            raise

    notify([user_id], "documents", "created", {"id": document["id"]})
    return public_document(document)


@router.get("/storage")
def get_storage(authorization: str | None = Header(default=None)):
    """Resumen de cuota: {used, limit, available, documentCount, percent}.

    Lo calcula `storage_info_for_user` en `app/database.py`. El frontend lo usa
    para la barra de almacenamiento y para avisar antes de subir.
    """
    user_id = authenticated_user_id(authorization)
    return storage_info_for_user(user_id, MAX_STORAGE_PER_USER)


@router.get("")
def get_documents(
    materia: str | None = Query(default=None, max_length=120),
    authorization: str | None = Header(default=None),
):
    """Lista documentos propios; con `?materia=` filtra por asociación.

    NOTA: al filtrar por materia se incluyen documentos de OTROS usuarios que
    este haya asociado (biblioteca compartida). Ver `list_documents`.
    """
    user_id = authenticated_user_id(authorization)
    return [public_document(document) for document in list_documents(user_id, materia)]


@router.get("/public")
def get_public_documents(
    authorization: str | None = Header(default=None),
):
    """Lista documentos de otros usuarios (biblioteca compartida).

    Añade `ownerName`/`ownerEmail` para poder mostrar el autor. Excluye los
    propios (esos ya vienen en `GET /documents`).
    CÓMO LIMITAR LA VISIBILIDAD: filtra en `list_documents_from_others`.
    """
    user_id = authenticated_user_id(authorization)
    documents = []
    for document in list_documents_from_others(user_id):
        item = public_document(document)
        item["ownerName"] = document.get("owner_name")
        item["ownerEmail"] = document.get("owner_email")
        documents.append(item)
    return documents


@router.get("/{document_id}/download")
def download_document(
    document_id: str,
    authorization: str | None = Header(default=None),
):
    """Descarga el archivo físico de un documento por id (de cualquier dueño).

    - 404 si el metadato no existe.
    - Valida path traversal y que el archivo siga en disco.
    - `FileResponse` con el `content_type` y el nombre original guardados; así el
      navegador propone el nombre correcto al guardar.
    EFECTO: lectura de disco.
    CÓMO RESTRINGIR A DOCUMENTOS PROPIOS: usa `get_document(user_id, ...)` en vez
    de `get_document_by_id`.
    """
    user_id = authenticated_user_id(authorization)
    document = get_document_by_id(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="No se encontró el documento.")
    path = (UPLOADS_PATH / document["storage_path"]).resolve()
    if not path.is_relative_to(UPLOADS_PATH.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="El archivo ya no está disponible.")
    return FileResponse(path, media_type=document["content_type"], filename=document["filename"])


@router.post("/{document_id}/materias", status_code=status.HTTP_204_NO_CONTENT)
def add_document_to_materia(
    document_id: str,
    payload: MateriaAssociation,
    authorization: str | None = Header(default=None),
):
    """Asocia un documento (propio o compartido) a una materia del usuario.

    Idempotente gracias al upsert de `associate_document`. 404 si el documento
    no existe en absoluto.
    """
    user_id = authenticated_user_id(authorization)
    if not associate_document(user_id, payload.materia.strip(), document_id):
        raise HTTPException(status_code=404, detail="No se encontró el documento.")


@router.delete("/{document_id}/materias", status_code=status.HTTP_204_NO_CONTENT)
def remove_document_from_materia(
    document_id: str,
    materia: str = Query(min_length=1, max_length=120),
    authorization: str | None = Header(default=None),
):
    """Quita la asociación de un documento con una materia (no borra el archivo).

    `materia` llega como query param (`?materia=...`). Devuelve 204 siempre que
    el documento exista.
    """
    user_id = authenticated_user_id(authorization)
    if not get_document_by_id(document_id):
        raise HTTPException(status_code=404, detail="No se encontró el documento.")
    remove_document_association(user_id, materia, document_id)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_document(
    document_id: str,
    authorization: str | None = Header(default=None),
):
    """Borra un documento PROPIO: metadatos, asociaciones y archivo físico.

    - 404 si el documento no existe o no es del usuario (`delete_document`
      filtra por `user_id`).
    - Borra las asociaciones de TODOS los usuarios (Mongo no tiene FK) y luego
      el archivo del disco.
    - Emite SSE `documents.deleted` con el id para que el frontend lo quite.
    EFECTO: borrados en `documents` y `materia_documents` + disco.
    """
    user_id = authenticated_user_id(authorization)
    document = delete_document(user_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="No se encontró el documento.")
    (UPLOADS_PATH / document["storage_path"]).unlink(missing_ok=True)
    notify([user_id], "documents", "deleted", {"id": document_id})
