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
MAX_STORAGE_PER_USER = 3 * 1024 * 1024
# El tamano maximo por archivo nunca puede superar la cuota total del usuario.
MAX_FILE_SIZE = MAX_STORAGE_PER_USER
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}

_locks_guard = threading.Lock()
_user_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _user_lock(user_id: str) -> threading.Lock:
    with _locks_guard:
        return _user_locks[user_id]


class MateriaAssociation(BaseModel):
    materia: str = Field(min_length=1, max_length=120)


def authenticated_user_id(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    user_id = user_id_for_token(authorization.removeprefix("Bearer ").strip())
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión ya no es válida. Inicia sesión de nuevo.")
    return user_id


def public_document(document: dict) -> dict:
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
    user_id = authenticated_user_id(authorization)
    filename = Path(file.filename or "").name.strip()
    extension = Path(filename).suffix.lower()
    if not filename or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido.")

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
            destination.unlink(missing_ok=True)
            raise

    notify([user_id], "documents", "created", {"id": document["id"]})
    return public_document(document)


@router.get("/storage")
def get_storage(authorization: str | None = Header(default=None)):
    user_id = authenticated_user_id(authorization)
    return storage_info_for_user(user_id, MAX_STORAGE_PER_USER)


@router.get("")
def get_documents(
    materia: str | None = Query(default=None, max_length=120),
    authorization: str | None = Header(default=None),
):
    user_id = authenticated_user_id(authorization)
    return [public_document(document) for document in list_documents(user_id, materia)]


@router.get("/public")
def get_public_documents(
    authorization: str | None = Header(default=None),
):
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
    user_id = authenticated_user_id(authorization)
    if not associate_document(user_id, payload.materia.strip(), document_id):
        raise HTTPException(status_code=404, detail="No se encontró el documento.")


@router.delete("/{document_id}/materias", status_code=status.HTTP_204_NO_CONTENT)
def remove_document_from_materia(
    document_id: str,
    materia: str = Query(min_length=1, max_length=120),
    authorization: str | None = Header(default=None),
):
    user_id = authenticated_user_id(authorization)
    if not get_document_by_id(document_id):
        raise HTTPException(status_code=404, detail="No se encontró el documento.")
    remove_document_association(user_id, materia, document_id)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_document(
    document_id: str,
    authorization: str | None = Header(default=None),
):
    user_id = authenticated_user_id(authorization)
    document = delete_document(user_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="No se encontró el documento.")
    (UPLOADS_PATH / document["storage_path"]).unlink(missing_ok=True)
    notify([user_id], "documents", "deleted", {"id": document_id})