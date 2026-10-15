"""Migra los datos de studyfinder.db (SQLite) a MongoDB Atlas.

Ejecutar desde backend/ (para que el paquete app. resuelva):

    python -m scripts.migrate_sqlite_to_mongo

La conexión se toma de MONGODB_URL / MONGODB_DATABASE en backend/.env.
El script es idempotente: los correos ya registrados en Mongo se saltan.
"""

from pathlib import Path
import sqlite3

from pymongo.errors import DuplicateKeyError

from app.database import (
    documents_collection,
    materia_documents_collection,
    sessions_collection,
    users_collection,
)

DATABASE_PATH = Path(__file__).resolve().parent.parent / "studyfinder.db"


def rows(collection_name: str, connection: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {collection_name}").fetchall()]


def main() -> None:
    if not DATABASE_PATH.exists():
        print("No hay studyfinder.db que migrar; la app ya usa MongoDB.")
        return

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    usuarios = 0
    for fila in rows("users", connection):
        usuario = dict(fila)
        usuario["_id"] = usuario.pop("id")
        try:
            users_collection.insert_one(usuario)
            usuarios += 1
        except DuplicateKeyError:
            pass

    sesiones = 0
    for fila in rows("auth_sessions", connection):
        try:
            sessions_collection.insert_one(fila)
            sesiones += 1
        except DuplicateKeyError:
            pass

    documentos = 0
    for fila in rows("documents", connection):
        try:
            documents_collection.insert_one(fila)
            documentos += 1
        except DuplicateKeyError:
            pass

    materias = 0
    for fila in rows("materia_documents", connection):
        try:
            materia_documents_collection.insert_one(fila)
            materias += 1
        except DuplicateKeyError:
            pass

    connection.close()
    print(
        f"Migración completada: {usuarios} usuarios, {sesiones} sesiones, "
        f"{documentos} documentos, {materias} asociaciones de materia."
    )


if __name__ == "__main__":
    main()