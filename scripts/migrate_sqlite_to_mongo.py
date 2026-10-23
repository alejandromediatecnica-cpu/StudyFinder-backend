"""
Migra los datos de studyfinder.db (SQLite) a MongoDB Atlas.

Ejecutar desde la raíz del repositorio (para que el paquete app. resuelva):

    uv run python -m scripts.migrate_sqlite_to_mongo

La conexión se toma de MONGODB_URL / MONGODB_DATABASE en .env.
El script es idempotente: los correos ya registrados en Mongo se saltan.

POR QUÉ ES IDEMPOTENTE
  Los índices únicos (`users.email`, `users.name_key`, `auth_sessions.token`,
  `materia_documents` compuesto) hacen que un `insert_one` repetido lance
  `DuplicateKeyError`, que aquí se ignora. Puedes ejecutarlo varias veces.

NOTA SOBRE LOS IDS
  El `id` entero de SQLite se conserva como `_id` (ver `usuario["_id"]`), y por
  eso el resto de la app usa `id_query()` para convivir con ids legacy.

CÓMO MIGRAR UNA TABLA NUEVA
  - Copia el bloque de una tabla existente (`rows` + bucle + contador) y añade su
    colección al import.
CÓMO BORRAR SQLITE DESPUÉS
  - Elimina `studyfinder.db` cuando la migración esté verificada; el script solo
    lee.
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

# Ruta del SQLite histórico: <repo>/studyfinder.db.
DATABASE_PATH = Path(__file__).resolve().parent.parent / "studyfinder.db"


def rows(collection_name: str, connection: sqlite3.Connection) -> list[dict]:
    """Devuelve todas las filas de una tabla como diccionarios.

    `row_factory = sqlite3.Row` (en `main`) permite `dict(row)`.
    ADVERTENCIA: `collection_name` se interpola en el SQL; solo se usa con
    nombres fijos del propio script.
    """
    return [dict(row) for row in connection.execute(f"SELECT * FROM {collection_name}").fetchall()]


def main() -> None:
    """Ejecuta la migración completa e imprime un resumen.

    Orden: usuarios -> sesiones -> documentos -> asociaciones de materia.
    Usuarios primero para que las referencias (`user_id`) ya existan.
    EFECTOS: inserciones en Mongo; no modifica el SQLite.
    """
    if not DATABASE_PATH.exists():
        print("No hay studyfinder.db que migrar; la app ya usa MongoDB.")
        return

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    # --- Usuarios: `id` de SQLite pasa a ser `_id` de Mongo. ---
    usuarios = 0
    for fila in rows("users", connection):
        usuario = dict(fila)
        usuario["_id"] = usuario.pop("id")
        try:
            users_collection.insert_one(usuario)
            usuarios += 1
        except DuplicateKeyError:
            # Ya existía (email/name_key único): se salta.
            pass

    # --- Sesiones (tokens Bearer) tal cual. ---
    sesiones = 0
    for fila in rows("auth_sessions", connection):
        try:
            sessions_collection.insert_one(fila)
            sesiones += 1
        except DuplicateKeyError:
            pass

    # --- Metadatos de documentos. ---
    documentos = 0
    for fila in rows("documents", connection):
        try:
            documents_collection.insert_one(fila)
            documentos += 1
        except DuplicateKeyError:
            pass

    # --- Relación documento <-> materia. ---
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
