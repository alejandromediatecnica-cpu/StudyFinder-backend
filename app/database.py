"""
Capa de acceso a datos (MongoDB) y utilidades de persistencia.

Aquí NO hay ORM: se exponen las colecciones de pymongo y funciones helper que
los routers usan. Todas las "relaciones" son referencias por valor (`user_id`,
`document_id`, etc.); las cascadas están implementadas a mano.

Mapa de colecciones:
  - users                    -> cuentas (email y name_key únicos)
  - auth_sessions            -> tokens Bearer opacos
  - documents                -> metadatos de archivos subidos
  - materia_documents        -> relación (usuario, materia, documento)
  - tasks                    -> tareas
  - calendar_events          -> eventos (pueden vincularse a una tarea por task_id)
  - password_verifications   -> tokens temporales del cambio de contraseña
  - contacts                 -> solicitudes/relaciones de contacto
  - messages                 -> mensajes de chat

CÓMO CAMBIAR DE BASE DE DATOS O COLECCIONES:
  - La conexión se define en `app/config.py` (`MONGODB_URL`, `MONGODB_DATABASE`).
  - Si añades una colección, declárala aquí junto a las demás y crea sus índices
    en `initialize_database()`.
"""

import logging
import re
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import MONGODB_DATABASE, MONGODB_URL, UPLOADS_DIR

logger = logging.getLogger(__name__)

# Carpeta base de archivos. Se re-exporta para que los routers la importen desde
# `app.database` (algunos tests la parchean aquí).
UPLOADS_PATH = UPLOADS_DIR
# PyMongo espera 30 s por defecto antes de rendirse; en serverless eso agota la
# función. 10 s es un compromiso razonable.
SERVER_SELECTION_TIMEOUT_MS = 10_000

# Cliente singleton (se crea perezosamente en `get_client`).
_client: MongoClient | None = None


# ---------------------------------------------------------------------------
# IDs LEGACY (migración desde SQLite)
# ---------------------------------------------------------------------------
def id_query(valor):
    """Normaliza un id para compararlo con `_id`.

    La base tiene ids legacy (enteros o sus strings, de la migracion SQLite)
    mezclados con ObjectId nuevos. Devuelve el ObjectId si es valido, si no el
    entero cuando el valor es numerico, y en ultimo caso el valor original.

    ÚSALO SIEMPRE que compares un id recibido por la API contra `_id`. Nunca
    hagas `ObjectId(user_id)` directamente: rompería con ids legacy como "3".
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


# ---------------------------------------------------------------------------
# CONEXIÓN
# ---------------------------------------------------------------------------
def get_client() -> MongoClient:
    """Devuelve el cliente Mongo singleton, creándolo la primera vez.

    EFECTO: crea la conexión con `serverSelectionTimeoutMS` corto. Reutilizar el
    cliente es importante: pymongo mantiene un pool de conexiones.
    """
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
    return _client


def get_database():
    """Devuelve la base de datos configurada."""
    return get_client()[MONGODB_DATABASE]


# Base y colecciones globales. Se resuelven en tiempo de importación; por eso
# los tests parchean `pymongo.MongoClient` ANTES de importar este módulo.
db = get_database()

users_collection = db["users"]
sessions_collection = db["auth_sessions"]
documents_collection = db["documents"]
materia_documents_collection = db["materia_documents"]
tasks_collection = db["tasks"]
calendar_events_collection = db["calendar_events"]
password_verifications_collection = db["password_verifications"]
contacts_collection = db["contacts"]
messages_collection = db["messages"]


# ---------------------------------------------------------------------------
# NORMALIZACIÓN DE NOMBRES
# ---------------------------------------------------------------------------
def normalize_name_key(name: str) -> str:
    """Clave de unicidad para el nombre de usuario.

    Se ignoran mayúsculas, acentos y espacios repetidos para que "José  Pérez"
    y "jose perez" no puedan registrarse como cuentas distintas.

    Pasos: NFKD -> quita diacríticos -> colapsa espacios -> casefold.
    EFECTO: el resultado se guarda en `users.name_key` (índice único sparse) y se
    usa para detectar duplicados en signup y al cambiar el nombre.
    """
    normalized = unicodedata.normalize("NFKD", (name or "").strip())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).casefold()


# ---------------------------------------------------------------------------
# INICIALIZACIÓN E ÍNDICES
# ---------------------------------------------------------------------------
def initialize_database() -> None:
    """Crea los índices necesarios. Idempotente.

    Índices clave:
      - users.email único y users.name_key único sparse (unicidad de cuentas).
      - auth_sessions.token único (búsqueda de sesión).
      - password_verifications.token único.
      - materia_documents (user_id, materia, document_id) único (evita duplicados).
      - documents (user_id, created_at), tasks (user_id, due_date),
        calendar_events (user_id, date) para consultas por usuario.

    IMPORTANTE: no crea `UPLOADS_PATH` (en Vercel el bundle es de solo lectura y
    fallaría al importar); las rutas de subida crean la carpeta al escribir.
    Si Mongo no responde, NO tumba la app: registra el error y sigue (las rutas
    con datos devolverán 503 vía el handler de `ConnectionFailure`).

    CÓMO AÑADIR UN ÍNDICE: agrégalo dentro del `try`, con
    `<coleccion>.create_index(...)`.
    """
    try:
        users_collection.create_index("email", unique=True)
        users_collection.create_index("name_key", unique=True, sparse=True)
        sessions_collection.create_index("token", unique=True)
        password_verifications_collection.create_index("token", unique=True)
        materia_documents_collection.create_index(
            [("user_id", ASCENDING), ("materia", ASCENDING), ("document_id", ASCENDING)],
            unique=True,
        )
        documents_collection.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])
        tasks_collection.create_index([("user_id", ASCENDING), ("due_date", ASCENDING)])
        calendar_events_collection.create_index([("user_id", ASCENDING), ("date", ASCENDING)])
    except PyMongoError as error:
        logger.error(
            "No se pudo inicializar MongoDB (base %r): %s: %s. "
            "Revisa MONGODB_URL y que Atlas > Network Access permita la conexión.",
            MONGODB_DATABASE,
            type(error).__name__,
            error,
        )


# Se ejecuta al importar el módulo (y otra vez en el startup de FastAPI).
initialize_database()


# ---------------------------------------------------------------------------
# SESIONES (tokens Bearer)
# ---------------------------------------------------------------------------
def create_session(user_id: str) -> str:
    """Crea una sesión nueva y devuelve su token.

    El token es opaco: dos `uuid4().hex` concatenados (64 hex = 32 bytes de
    entropía). NO expira por sí solo; se revoca al cerrar sesión, cambiar
    contraseña o borrar la cuenta.
    EFECTO: inserta en `auth_sessions` {token, user_id, created_at}.
    """
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
    """Devuelve el `user_id` de una sesión, o None si el token no existe.

    Usado por SSE y por el helper de documentos (que no depende de `deps.py`).
    """
    session = sessions_collection.find_one({"token": token})
    return session["user_id"] if session else None


def revoke_session(token: str) -> None:
    """Borra una sesión concreta (logout)."""
    sessions_collection.delete_one({"token": token})


def revoke_user_sessions(user_id: str, keep_token: str | None = None) -> None:
    """Borra todas las sesiones de un usuario.

    Si se pasa `keep_token`, conserva esa sesión (útil si quisieras mantener la
    actual). Hoy `change_password` revoca TODAS y crea una nueva.
    """
    query: dict = {"user_id": user_id}
    if keep_token:
        query["token"] = {"$ne": keep_token}
    sessions_collection.delete_many(query)


# ---------------------------------------------------------------------------
# DOCUMENTOS (metadatos; los bytes viven en disco)
# ---------------------------------------------------------------------------
def create_document(user_id: str, filename: str, storage_path: str, content_type: str, size: int) -> dict:
    """Inserta el metadato de un documento ya escrito en disco.

    `id` es un uuid4 hex independiente del `_id` de Mongo (se usa en las URLs).
    EFECTO: inserta en `documents` y devuelve el dict insertado.
    """
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
    """Lista documentos del usuario (o los asociados a una materia).

    - Sin `materia`: todos los documentos propios, más recientes primero.
    - Con `materia`: busca las asociaciones del usuario para esa materia y
      devuelve esos documentos por `id`. NOTA: el filtro por materia NO filtra
      por propietario, porque un usuario puede asociar documentos de otros
      (biblioteca compartida).
    """
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
    """Devuelve un documento solo si pertenece al usuario."""
    return documents_collection.find_one({"id": document_id, "user_id": user_id})


def get_document_by_id(document_id: str) -> dict | None:
    """Devuelve un documento por id SIN filtrar por propietario.

    Se usa para descargas y asociaciones a materias (biblioteca compartida).
    """
    return documents_collection.find_one({"id": document_id})


def total_storage_for_user(user_id: str) -> int:
    """Suma los bytes de todos los documentos del usuario (para la cuota)."""
    total = 0
    for document in documents_collection.find({"user_id": user_id}, {"size": 1}):
        total += document.get("size", 0)
    return total


def storage_info_for_user(user_id: str, limit: int) -> dict:
    """Resumen de cuota para `GET /documents/storage`.

    RETORNO: {used, limit, available (>=0), documentCount, percent (1 decimal)}.
    """
    used = total_storage_for_user(user_id)
    return {
        "used": used,
        "limit": limit,
        "available": max(limit - used, 0),
        "documentCount": documents_collection.count_documents({"user_id": user_id}),
        "percent": round(used * 100 / limit, 1) if limit else 0,
    }


def list_documents_from_others(exclude_user_id: str) -> list[dict]:
    """Lista documentos de OTROS usuarios y les añade nombre/email del dueño.

    Se usa en `GET /documents/public` (biblioteca compartida). Hace una consulta
    por lote de usuarios para evitar N+1.
    """
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
    """Asocia un documento a una materia del usuario (idempotente).

    Valida que el documento exista (cualquier dueño). Usa upsert con
    `$setOnInsert` para que repetir la asociación no duplique (hay índice único).
    RETORNO: False si el documento no existe; True si se asoció o ya estaba.
    """
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
    """Quita la asociación (usuario, materia, documento). No borra el documento."""
    materia_documents_collection.delete_one(
        {"user_id": user_id, "materia": materia, "document_id": document_id}
    )


def delete_document(user_id: str, document_id: str) -> dict | None:
    """Borra un documento propio y SUS asociaciones a materias (de todos).

    Cascada manual (Mongo no tiene FK): primero `materia_documents`, luego
    `documents`. NO borra el archivo físico; eso lo hace el router.
    RETORNO: el documento borrado (para conocer `storage_path`), o None si no era
    del usuario / no existía.
    """
    document = documents_collection.find_one({"id": document_id, "user_id": user_id})
    if not document:
        return None
    materia_documents_collection.delete_many({"document_id": document_id})
    documents_collection.delete_one({"id": document_id, "user_id": user_id})
    return document


# ---------------------------------------------------------------------------
# BAJA DE CUENTA (borrado en cascada)
# ---------------------------------------------------------------------------
def delete_user_data(user_id: str) -> tuple[dict | None, list[dict], list[str]]:
    """Borra la cuenta y todos los datos que le pertenecen.

    No elimina los mensajes que otros usuarios le enviaron porque son contenido
    de esos usuarios; solo se borran los mensajes escritos por la cuenta y sus
    archivos adjuntos.

    Borra: asociaciones a materias (propias o de sus documentos), documentos,
    tareas, eventos, mensajes propios, contactos (en cualquiera de los dos lados),
    sesiones, verificaciones y el usuario.

    RETORNO: (usuario, documentos, rutas de adjuntos) para que el router borre
    los archivos físicos del disco.
    """
    user = users_collection.find_one({"_id": id_query(user_id)})
    documents = list(documents_collection.find({"user_id": user_id}))
    document_ids = [document["id"] for document in documents]
    # Rutas de archivos adjuntos enviados por este usuario (para borrarlos del disco).
    attachments = [
        message["archivo"]
        for message in messages_collection.find(
            {"emisor_id": user_id, "archivo": {"$ne": None}}, {"archivo": 1}
        )
        if message.get("archivo")
    ]

    materia_documents_collection.delete_many(
        {
            "$or": [
                {"user_id": user_id},
                {"document_id": {"$in": document_ids}},
            ]
        }
    )
    documents_collection.delete_many({"user_id": user_id})
    tasks_collection.delete_many({"user_id": user_id})
    calendar_events_collection.delete_many({"user_id": user_id})
    messages_collection.delete_many({"emisor_id": user_id})
    contacts_collection.delete_many({"$or": [{"a_id": user_id}, {"b_id": user_id}]})
    sessions_collection.delete_many({"user_id": user_id})
    password_verifications_collection.delete_many({"user_id": user_id})
    users_collection.delete_one({"_id": id_query(user_id)})
    return user, documents, attachments
