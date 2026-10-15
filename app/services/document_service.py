from app.clients.document_client import DocumentClient

client = DocumentClient()


def _extract_url(paper: dict | None) -> str | None:
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
    data = await client.search_documents(query, per_page=8)

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
            "type": paper.get("type"),
            "source": ((paper.get("primary_location") or {}).get("source") or {}).get("display_name"),
        })

    return documents