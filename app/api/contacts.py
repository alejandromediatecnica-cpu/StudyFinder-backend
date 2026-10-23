"""
Contactos: solicitudes (enviar, listar entrantes/salientes), aceptar, rechazar y
listar contactos aceptados.

MODELO DE DATOS
  Cada relación entre dos usuarios se guarda UNA sola vez con el par ordenado
  (`a_id`, `b_id`) donde `a_id < b_id` (ver `_norm_id`). Esto evita duplicados
  A->B / B->A y permite un `find_one` directo. Además se guardan
  `usuario1_id`/`usuario2_id` (legacy, mismos valores que a/b) y
  `emisor_id`/`receptor_id` (quién inició la solicitud).
  Estados: `pendiente` -> `aceptado` | `rechazado`.

NOTA: este router mezcla nombres de campo en español y respuestas con claves
como `emisor`/`receptor`/`otro`, porque así los consume el frontend
(`MessagesService`/`ContactsService`).

CÓMO AÑADIR UN CONTACTO DIRECTO (sin solicitud)
  - Inserta con `estado="aceptado"` y emite `contacts.updated` a ambos.
CÓMO BORRAR UN CONTACTO
  - No hay endpoint; se puede añadir `DELETE /contacts/{contact_id}` aquí.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from bson import ObjectId

from app.database import contacts_collection, id_query, users_collection
from app.deps import get_current_user
from app.realtime import notify

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _norm_id(a, b):
    """Ordena el par de ids para que la relación sea canónica.

    Devuelve siempre `(menor, mayor)` comparando los strings. Así (A,B) y (B,A)
    apuntan al mismo documento.
    """
    return (a, b) if a < b else (b, a)


@router.post("/requests")
def send_request(payload: dict, current_user: dict = Depends(get_current_user)):
    """Envía (o reenvía) una solicitud de contacto a `usuario_id`.

    Casos:
      - Falta `usuario_id` -> 400; usuario inexistente -> 404; uno mismo -> 400.
      - Ya aceptados -> 409. Pendiente enviada por mí -> 409. Pendiente recibida
        de esa persona -> 409 (debe aceptarla desde sus solicitudes).
      - Si existía una relación `rechazado`, se REABRE como `pendiente` conmigo
        como emisor (permite reintentar tras un rechazo).
      - Si no existía, se inserta con estado `pendiente`.

    EFECTOS: insert/update en `contacts` y SSE `contacts.updated` a ambos.
    CÓMO IMPEDIR REINTENTOS: cambia la rama del `update_one` por un 409.
    CÓMO VALIDAR ENTRADA: hoy `payload` es un dict libre; podrías usar un modelo
    Pydantic con `usuario_id: str`.
    """
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

    # Par canónico + búsqueda de la relación existente.
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
        # Rechazada previamente: se reabre como pendiente, ahora yo soy el emisor.
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
    # Ambos refrescan sus listas de solicitudes.
    notify([yo_id, otro_id], "contacts", "updated", {"estado": "pendiente"})
    return {"ok": True}


@router.get("/requests/incoming")
def incoming_requests(current_user: dict = Depends(get_current_user)):
    """Solicitudes PENDIENTES que me han enviado, de más nueva a más antigua.

    Resuelve el nombre/email del emisor con una consulta por solicitud (n pequeño).
    RETORNO: [{id, emisor: {id, name, email}, created_at}].
    """
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
    """Solicitudes PENDIENTES que yo he enviado.

    RETORNO: [{id, receptor: {id, name, email}, created_at}].
    """
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
    """Acepta una solicitud pendiente dirigida a mí.

    - `req_id` es el `_id` de Mongo (ObjectId); si no es válido -> 400.
    - 404 si no existe o ya no está pendiente; 403 si el receptor no soy yo.
    EFECTOS: estado -> `aceptado`; SSE `contacts.updated` a ambos.
    A partir de aquí se habilita el chat (`_ensure_contact` en messages).
    """
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
    """Rechaza una solicitud pendiente dirigida a mí.

    Igual que `accept_request` pero estado -> `rechazado`. La relación se
    conserva: el emisor puede volver a enviarla (se reabre como pendiente).
    EFECTOS: estado -> `rechazado`; SSE `contacts.updated` a ambos.
    CÓMO BORRARLA EN VEZ DE MARCARLA: cambia el `update_one` por `delete_one`.
    """
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
    """Lista mis contactos ACEPTADOS, el más reciente primero.

    Para cada relación calcula la "otra parte" (`otro`) y añade nombre/email.
    RETORNO: [{contact_id, otro: {id, name, email}, updated_at}].
    Es la lista que alimenta el selector de destinatarios del chat.
    """
    yo_id = str(current_user["_id"])
    out = []
    for it in contacts_collection.find({"estado": "aceptado", "$or": [{"a_id": yo_id}, {"b_id": yo_id}]}).sort("updated_at", -1):
        a_id = it.get("a_id"); b_id = it.get("b_id")
        # La "otra" parte es la que no soy yo.
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
