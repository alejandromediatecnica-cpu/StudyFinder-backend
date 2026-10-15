from fastapi import Header, HTTPException, status

from app.database import id_query, sessions_collection, users_collection


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    token = authorization.removeprefix("Bearer ").strip()
    sid = sessions_collection.find_one({"token": token})
    if not sid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    user = users_collection.find_one({"_id": id_query(sid["user_id"])})
    if not user:
        sessions_collection.delete_one({"token": token})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    return user
