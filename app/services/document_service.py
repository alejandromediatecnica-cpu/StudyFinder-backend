"""
Servicio de búsqueda académica: adapta la respuesta cruda de OpenAlex al formato
que consume el frontend.

Cadena completa de una búsqueda:
  Router `app/api/search.py` -> `search_documents` -> `DocumentClient`
  -> `https://api.openalex.org/works` -> normalización aquí -> JSON al navegador.

CÓMO CAMBIAR EL NÚMERO DE RESULTADOS
  - `per_page=8` en `search_documents`.
CÓMO CAMBIAR LOS CAMPOS DEVUELTOS
  - El dict dentro del bucle (title, authors, year, url, type, source). Si
    añades uno que no esté en `select` del cliente, también hay que pedirlo allí.
CÓMO QUITAR EL FILTRO DE TEXTO COMPLETO
  - `filter="has_fulltext:true"` en `DocumentClient.search_documents`.
"""

from app.clients.document_client import DocumentClient

# Cliente sin estado: se instancia una vez al importar el módulo.
client = DocumentClient()


def _extract_url(paper: dict | None) -> str | None:
    """Elige la mejor URL para abrir el trabajo.

    Orden de preferencia:
      1. `primary_location.landing_page_url` (página oficial),
      2. `best_oa_location.landing_page_url` (acceso abierto),
      3. `primary_location.pdf_url`,
      4. `best_oa_location.pdf_url`,
      5. DOI (`ids.doi`, incluye https://doi.org/...).

    RETORNO: la URL o None si no hay ninguna. Tolera `paper` no-dict y campos
    ausentes (`or {}`).
    """
    if not isinstance(paper, dict):
        return None

    primary = paper.get("primary_location") or {}
    best = paper.get("best_oa_location") or {}
    return (
        primary.get("landing_page_url")
        or best.get("landing_page_url")
        or primary.get("pdf_url")
        or best.get("pdf_url")
        or (paper.get("ids") or {}).get("doi")
    )


def _extract_authors(paper: dict | None) -> list[str]:
    """Extrae los nombres de autores en orden de autoría.

    - Recorre `authorships[].author.display_name`.
    - Si no hay ninguno, devuelve `["Autor no disponible"]` (el frontend siempre
      muestra algo).
    CÓMO LIMITAR EL NÚMERO DE AUTORES: corta la lista devuelta (p. ej. `[:3]`).
    """
    if not isinstance(paper, dict):
        return ["Autor no disponible"]

    authors: list[str] = []
    for item in paper.get("authorships", []) or []:
        if not isinstance(item, dict):
            continue
        author = item.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(name)
    return authors or ["Autor no disponible"]


async def search_documents(query: str):
    """Busca en OpenAlex y devuelve una lista normalizada (máx. 8).

    Formato de cada elemento:
      {title, authors: [str], year, url, type, source}
    - Si OpenAlex devuelve algo inesperado (no dict), se devuelve [].
    - Los trabajos que no sean dict se ignoran.
    EFECTOS: una petición HTTP saliente (ver `DocumentClient`); no toca Mongo.
    CÓMO DEVOLVER EL OBJETO COMPLETO: retorna `results` directamente.
    """
    data = await client.search_documents(query, per_page=8)

    # Defensa ante respuestas no esperadas (p. ej. HTML de un proxy).
    if not isinstance(data, dict):
        return []

    results = data.get("results") or []
    documents = []

    for paper in results:
        if not isinstance(paper, dict):
            continue

        documents.append({
            "title": paper.get("display_name") or "Sin título",
            "authors": _extract_authors(paper),
            "year": paper.get("publication_year"),
            "url": _extract_url(paper),
            # Tipo OpenAlex ("article", "book", "preprint"...).
            "type": paper.get("type"),
            # Nombre de la revista/editorial, si existe.
            "source": ((paper.get("primary_location") or {}).get("source") or {}).get("display_name"),
        })

    return documents
