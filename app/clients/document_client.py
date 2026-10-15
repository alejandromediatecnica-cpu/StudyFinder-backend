import httpx
from fastapi import HTTPException


class DocumentClient:

    async def search_documents(self, query: str, per_page: int = 10):
        clean_query = (query or "").strip()

        if not clean_query:
            raise HTTPException(status_code=400, detail="Debes enviar un texto de búsqueda válido.")

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
                        "select": "id,display_name,publication_year,authorships,primary_location,best_oa_location,ids",
                        "sort": "relevance_score:desc",
                    },
                    headers={
                        "User-Agent": "StudyFinderApp/1.0 (mailto:studyfinder_dev@example.com)",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                return response.json()

            except httpx.ConnectError:
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
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"OpenAlex respondió con error: {e.response.text}",
                )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"Error de red en OpenAlex: {str(e)}",
                )
