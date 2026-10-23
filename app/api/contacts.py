from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from bson import ObjectId

from app.database import contacts_collection, id_query, users_collection
from app.deps import get_current_user
from app.realtime import notify

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _norm_id(a, b):
    return (a, b) if a < b else (b, a)


@router.post("/requests")
def send_request(payload: dict, current_user: dict = Depends(get_current_user)):
    usuario_id = payload.get("usuario_id")
    if not usuario_id:
        raise HTTPException(status_code=400, detail="Usuario requerido")
    otro = users_collection.find_one({"_id": id_query(usuario_id)})
    if not otro:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    yo_id = str(current_user["_id"])
    otro_id = str(otro["_id"])
    if yo_id == otro_id:
        raise HTTPException(status_code=400, detail="No puedes agregarte a ti mismo")
    a, b = _norm_id(yo_id, otro_id)
    existing = contacts_collection.find_one({"a_id": a, "b_id": b})
    if existing:
        estado = existing.get("estado")
        if estado == "aceptado":
            raise HTTPException(status_code=409, detail="Ya son contactos")
        if estado == "pendiente" and existing.get("emisor_id") == yo_id:
            raise HTTPException(status_code=409, detail="Solicitud ya enviada")
        if estado == "pendiente" and existing.get("emisor_id") == otro_id:
            raise HTTPException(status_code=409, detail="Tienes una solicitud pendiente de este usuario")
        contacts_collection.update_one({"_id": existing["_id"]}, {"$set": {"estado": "pendiente", "emisor_id": yo_id, "receptor_id": otro_id, "updated_at": datetime.utcnow()}})
    else:
        contacts_collection.insert_one({
            "a_id": a, "b_id": b,
            "usuario1_id": a, "usuario2_id": b,
            "emisor_id": yo_id, "receptor_id": otro_id,
            "estado": "pendiente",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        })
    notify([yo_id, otro_id], "contacts", "updated", {"estado": "pendiente"})
    return {"ok": True}


@router.get("/requests/incoming")
def incoming_requests(current_user: dict = Depends(get_current_user)):
    yo_id = str(current_user["_id"])
    items = contacts_collection.find({"receptor_id": yo_id, "estado": "pendiente"}).sort("created_at", -1)
    out = []
    for it in items:
        emisor_id = it.get("emisor_id")
        u = users_collection.find_one({"_id": id_query(emisor_id)}) if emisor_id else None
        out.append({
            "id": str(it["_id"]),
            "emisor": {"id": emisor_id, "name": u.get("name", "") if u else "", "email": u.get("email", "") if u else ""},
            "created_at": it.get("created_at"),
        })
    return out


@router.get("/requests/outgoing")
def outgoing_requests(current_user: dict = Depends(get_current_user)):
    yo_id = str(current_user["_id"])
    items = contacts_collection.find({"emisor_id": yo_id, "estado": "pendiente"}).sort("created_at", -1)
    out = []
    for it in items:
        rec = it.get("receptor_id")
        u = users_collection.find_one({"_id": id_query(rec)}) if rec else None
        out.append({
            "id": str(it["_id"]),
            "receptor": {"id": rec, "name": u.get("name", "") if u else "", "email": u.get("email", "") if u else ""},
            "created_at": it.get("created_at"),
        })
    return out


@router.post("/requests/{req_id}/accept")
def accept_request(req_id: str, current_user: dict = Depends(get_current_user)):
    try:
        rid = ObjectId(req_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID invalido")
    it = contacts_collection.find_one({"_id": rid})
    if not it or it.get("estado") != "pendiente":
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if it.get("receptor_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="No autorizado")
    contacts_collection.update_one({"_id": rid}, {"$set": {"estado": "aceptado", "updated_at": datetime.utcnow()}})
    notify([it.get("a_id"), it.get("b_id")], "contacts", "updated", {"estado": "aceptado"})
    return {"ok": True}


@router.post("/requests/{req_id}/reject")
def reject_request(req_id: str, current_user: dict = Depends(get_current_user)):
    try:
        rid = ObjectId(req_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID invalido")
    it = contacts_collection.find_one({"_id": rid})
    if not it or it.get("estado") != "pendiente":
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if it.get("receptor_id") != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="No autorizado")
    contacts_collection.update_one({"_id": rid}, {"$set": {"estado": "rechazado", "updated_at": datetime.utcnow()}})
    notify([it.get("a_id"), it.get("b_id")], "contacts", "updated", {"estado": "rechazado"})
    return {"ok": True}


@router.get("/")
def list_contacts(current_user: dict = Depends(get_current_user)):
    yo_id = str(current_user["_id"])
    out = []
    for it in contacts_collection.find({"estado": "aceptado", "$or": [{"a_id": yo_id}, {"b_id": yo_id}]}).sort("updated_at", -1):
        a_id = it.get("a_id"); b_id = it.get("b_id")
        otro_id = b_id if a_id == yo_id else a_id
        if not otro_id:
            continue
        u = users_collection.find_one({"_id": id_query(otro_id)})
        out.append({
            "contact_id": str(it["_id"]),
            "otro": {"id": otro_id, "name": u.get("name", "") if u else "", "email": u.get("email", "") if u else ""},
            "updated_at": it.get("updated_at"),
        })
    return out
