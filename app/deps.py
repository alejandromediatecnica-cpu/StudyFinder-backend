"""
Dependencia de autenticación reutilizable por todos los routers.

`get_current_user` se usa como `Depends(get_current_user)` en los endpoints que
requieren sesión. Lee la cabecera `Authorization: Bearer <token>`, valida el
token contra la colección `auth_sessions` y devuelve el documento del usuario.

CÓMO CAMBIAR LA AUTENTICACIÓN (p. ej. a JWT):
  - Sustituye el cuerpo de `get_current_user` por la validación de JWT y sigue
    devolviendo el documento de usuario desde `users_collection`. El resto de la
    app NO cambia porque todos dependen de esta función.
CÓMO HACER QUE UN ENDPOINT SEA PÚBLICO:
  - Quita su `Depends(get_current_user)` (o el helper equivalente de documentos).
"""

from fastapi import Header, HTTPException, status

from app.database import id_query, sessions_collection, users_collection


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """Resuelve el usuario autenticado a partir del Bearer token.

    Pasos:
      1. Exige cabecera con formato `Bearer ...`; si no, 401.
      2. Extrae el token y busca la sesión en `auth_sessions`.
      3. Carga el usuario por `_id` (usando `id_query`, que soporta ids legacy).
      4. Si el usuario ya no existe, borra la sesión huérfana y devuelve 401.

    EFECTOS SECUNDARIOS: lectura en `auth_sessions` y `users`; puede borrar una
    sesión huérfana. No escribe nada más.
    RETORNO: el documento completo del usuario (dict de Mongo).
    """
    # 1) Formato de la cabecera.
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")

    # 2) Token -> sesión.
    token = authorization.removeprefix("Bearer ").strip()
    sid = sessions_collection.find_one({"token": token})
    if not sid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")

    # 3) Sesión -> usuario. `id_query` normaliza ids enteros (legacy SQLite) y ObjectId.
    user = users_collection.find_one({"_id": id_query(sid["user_id"])})

    # 4) Sesión apuntando a un usuario borrado: se limpia y se rechaza.
    if not user:
        sessions_collection.delete_one({"token": token})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    return user
