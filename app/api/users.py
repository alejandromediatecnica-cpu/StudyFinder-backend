import re
from fastapi import APIRouter, Depends
from app.database import users_collection
from app.deps import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": str(current_user["_id"]),
        "name": current_user["name"],
        "email": current_user["email"],
    }


@router.get("/search")
def search_users(nombre: str, current_user: dict = Depends(get_current_user)):
    """Busca usuarios por nombre (parcial, insensible a mayusculas) para
    agregarlos como contactos. Excluye al propio usuario."""
    termino = (nombre or "").strip()
    if len(termino) < 2:
        return []
    filtro = {
        "name": {"$regex": re.escape(termino), "$options": "i"},
        "_id": {"$ne": current_user["_id"]},
    }
    return [
        {"id": str(u["_id"]), "name": u.get("name", ""), "email": u.get("email", "")}
        for u in users_collection.find(filtro).limit(20)
    ]
