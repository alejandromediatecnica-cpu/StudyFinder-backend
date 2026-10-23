"""
Búsqueda pública de documentos académicos (OpenAlex).

`GET /search?q=...` NO requiere sesión: cualquiera puede buscar. La lógica vive
en `app/services/document_service.py:search_documents`, que a su vez usa
`app/clients/document_client.py` para llamar a la API de OpenAlex.

CÓMO CAMBIAR LA FUENTE (Crossref, Semantic Scholar...)
  - Reemplaza el cliente en `app/clients/document_client.py`; este router no
    cambia.
CÓMO EXIGIR SESIÓN
  - Añade `Depends(get_current_user)` al parámetro de la función.
"""

from fastapi import APIRouter

from app.services.document_service import search_documents

# Router SIN prefijo: la ruta final es `/search`.
router = APIRouter()


@router.get("/search")
async def search(q: str):
    """Devuelve hasta 8 resultados con texto completo para la consulta `q`.

    Respuesta: lista de `{title, authors, year, url, pdf_url, ...}` normalizada
    por el cliente (ver el servicio). `q` vacío lo gestiona OpenAlex.
    CÓMO AÑADIR FILTROS (año, área): añade parámetros de query aquí y propágalos
    a `search_documents`.
    """
    return await search_documents(q)
