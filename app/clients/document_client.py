"""
Cliente HTTP de OpenAlex para búsqueda de trabajos académicos.

OpenAlex es una API pública y gratuita (no requiere token). Solo pide un
`User-Agent` identificativo para el "polite pool".

CONSTRUCCIÓN DE LA CONSULTA
  - El texto se recorta y se le quitan comillas simples/dobles.
  - Las palabras se unen con " AND " para exigir que aparezcan todas.
    (Si prefieres OR o búsqueda libre, cambia el separador.)
  - `filter=has_fulltext:true` limita a trabajos con texto completo.
  - `sort=relevance_score:desc` ordena por relevancia.
  - `select` pide solo los campos usados por el servicio (respuesta más pequeña).

MANEJO DE ERRORES
  Cada fallo se traduce a un HTTPException con código y mensaje en español:
    400 sin texto | 503 sin conexión | 504 timeout | código de OpenAlex | 503 red.

CÓMO CAMBIAR DE FUENTE (Crossref, Semantic Scholar...)
  - Reemplaza la URL, params y el `select` por los de la nueva API y adapta
    `_extract_*` en `document_service.py`.
CÓMO AÑADIR UN FILTRO (año, área)
  - Añade un parámetro a `search_documents` y combínalo en `filter` con comas,
    p. ej. `f"has_fulltext:true,from_publication_date:{year}-01-01"`.
"""

import httpx
from fastapi import HTTPException


class DocumentClient:

    async def search_documents(self, query: str, per_page: int = 10):
        """Ejecuta la búsqueda en OpenAlex y devuelve el JSON crudo.

        PARÁMETROS:
          - query: texto del usuario (se valida no vacío).
          - per_page: resultados por página (el servicio usa 8).
        RETORNO: dict de OpenAlex con `results` (u otra cosa si la API cambia).
        EFECTOS: petición HTTP GET con timeout de 20 s; crea un `AsyncClient`
        por llamada (sencillo; para alto tráfico, reutiliza un cliente global).
        """
        clean_query = (query or "").strip()

        if not clean_query:
            raise HTTPException(status_code=400, detail="Debes enviar un texto de búsqueda válido.")

        # Quita comillas (evitan sintaxis rara en OpenAlex) y une con AND.
        clean_query = clean_query.replace('"', '').replace("'", "").strip()
        search_terms = " AND ".join(clean_query.split())

        async with httpx.AsyncClient(timeout=20.0) as client:
            base_url = "https://api.openalex.org"

            try:
                response = await client.get(
                    f"{base_url}/works",
                    params={
                        "search": search_terms,
                        "per_page": per_page,
                        "filter": "has_fulltext:true",
                        # Solo los campos que consume `document_service`.
                        "select": "id,display_name,publication_year,authorships,primary_location,best_oa_location,ids",
                        "sort": "relevance_score:desc",
                    },
                    headers={
                        # Identificación del cliente (buena práctica de OpenAlex).
                        "User-Agent": "StudyFinderApp/1.0 (mailto:studyfinder_dev@example.com)",
                        "Accept": "application/json",
                    },
                )
                # Lanza HTTPStatusError si OpenAlex responde 4xx/5xx.
                response.raise_for_status()
                return response.json()

            except httpx.ConnectError:
                # DNS/red caída: no se pudo ni establecer conexión.
                raise HTTPException(
                    status_code=503,
                    detail="No se pudo conectar con OpenAlex. Revisa tu conexión a internet.",
                )
            except httpx.TimeoutException:
                raise HTTPException(
                    status_code=504,
                    detail="La búsqueda tardó demasiado en responder. Inténtalo de nuevo.",
                )
            except httpx.HTTPStatusError as e:
                # Se propaga el código de OpenAlex con su cuerpo como detalle.
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"OpenAlex respondió con error: {e.response.text}",
                )
            except httpx.RequestError as e:
                # Cualquier otro fallo de transporte (TLS, conexión cortada...).
                raise HTTPException(
                    status_code=503,
                    detail=f"Error de red en OpenAlex: {str(e)}",
                )
