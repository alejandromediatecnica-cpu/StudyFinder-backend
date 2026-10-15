import logging
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import MONGODB_DATABASE, MONGODB_URL, UPLOADS_DIR

logger = logging.getLogger(__name__)

UPLOADS_PATH = UPLOADS_DIR
# PyMongo espera 30 s por defecto antes de rendirse; en serverless eso agota la función.
SERVER_SELECTION_TIMEOUT_MS = 10_000

_client: MongoClient | None = None


def id_query(valor):
    """Normaliza un id para compararlo con `_id`.

    La base tiene ids legacy (enteros o sus strings, de la migracion SQLite)
    mezclados con ObjectId nuevos. Devuelve el ObjectId si es valido, si no el
    entero cuando el valor es numerico, y en ultimo caso el valor original.
    """
    if isinstance(valor, ObjectId):
        return valor
    try:
        return ObjectId(valor)
    except (InvalidId, TypeError):
        pass
    try:
        return int(valor)
    except (TypeError, ValueError):
        return valor


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
    return _client


def get_database():
    return get_client()[MONGODB_DATABASE]


db = get_database()

users_collection = db["users"]
sessions_collection = db["auth_sessions"]
documents_collection = db["documents"]
materia_documents_collection = db["materia_documents"]


def initialize_database() -> None:
    # No crea UPLOADS_PATH: en Vercel el bundle es de solo lectura y fallaría al importar.
    # Las rutas de subida crean la carpeta al escribir el primer archivo.
    # Si MongoDB no responde, la app arranca igualmente (las rutas con datos devolverán 503)
    # en vez de tumbar todo el proceso al importar.
    try:
        users_collection.create_index("email", unique=True)
        materia_documents_collection.create_index(
            [("user_id", ASCENDING), ("materia", ASCENDING), ("document_id", ASCENDING)],
            unique=True,
        )
    except PyMongoError as error:
        logger.error(
            "No se pudo inicializar MongoDB (base %r): %s: %s. "
            "Revisa MONGODB_URL y que Atlas > Network Access permita la conexión.",
            MONGODB_DATABASE,
            type(error).__name__,
            error,
        )


initialize_database()


def create_session(user_id: str) -> str:
    token = uuid4().hex + uuid4().hex
    sessions_collection.insert_one(
        {
            "token": token,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return token


def user_id_for_token(token: str) -> str | None:
    session = sessions_collection.find_one({"token": token})
    return session["user_id"] if session else None


def revoke_session(token: str) -> None:
    sessions_collection.delete_one({"token": token})


def create_document(user_id: str, filename: str, storage_path: str, content_type: str, size: int) -> dict:
    document = {
        "id": uuid4().hex,
        "user_id": user_id,
        "filename": filename,
        "storage_path": storage_path,
        "content_type": content_type,
        "size": size,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    documents_collection.insert_one(document)
    return document


def list_documents(user_id: str, materia: str | None = None) -> list[dict]:
    if materia:
        related_ids = [
            relation["document_id"]
            for relation in materia_documents_collection.find({"user_id": user_id, "materia": materia})
        ]
        # Las asociaciones pueden apuntar a documentos ajenos compartidos, así
        # que el filtro por materia busca solo por id, no por propietario.
        query = {"id": {"$in": related_ids}}
    else:
        query = {"user_id": user_id}
    return list(documents_collection.find(query).sort("created_at", -1))


def get_document(user_id: str, document_id: str) -> dict | None:
    return documents_collection.find_one({"id": document_id, "user_id": user_id})


def get_document_by_id(document_id: str) -> dict | None:
    return documents_collection.find_one({"id": document_id})


def total_storage_for_user(user_id: str) -> int:
    total = 0
    for document in documents_collection.find({"user_id": user_id}, {"size": 1}):
        total += document.get("size", 0)
    return total


def list_documents_from_others(exclude_user_id: str) -> list[dict]:
    documents = list(
        documents_collection.find({"user_id": {"$ne": exclude_user_id}}).sort("created_at", -1)
    )
    owners = {str(user["_id"]): user for user in users_collection.find()}
    for document in documents:
        owner = owners.get(document["user_id"])
        document["owner_name"] = owner["name"] if owner else None
        document["owner_email"] = owner["email"] if owner else None
    return documents


def associate_document(user_id: str, materia: str, document_id: str) -> bool:
    if not documents_collection.find_one({"id": document_id}):
        return False
    # Upsert con $setOnInsert: asociar dos veces es idempotente (índice único).
    materia_documents_collection.update_one(
        {"user_id": user_id, "materia": materia, "document_id": document_id},
        {"$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return True


def remove_document_association(user_id: str, materia: str, document_id: str) -> None:
    materia_documents_collection.delete_one(
        {"user_id": user_id, "materia": materia, "document_id": document_id}
    )


def delete_document(user_id: str, document_id: str) -> dict | None:
    document = documents_collection.find_one({"id": document_id, "user_id": user_id})
    if not document:
        return None
    materia_documents_collection.delete_many({"document_id": document_id})
    documents_collection.delete_one({"id": document_id, "user_id": user_id})
    return document


contacts_collection = db["contacts"]
messages_collection = db["messages"]
