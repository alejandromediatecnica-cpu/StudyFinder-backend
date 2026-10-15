import asyncio

from app.services.document_service import _extract_authors, _extract_url, search_documents


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    def __init__(self, data):
        self.data = data

    async def search_documents(self, query, per_page=8):
        self.consulta = query
        self.per_page = per_page
        return self.data


def papel_razonable():
    return {
        "display_name": "Búsqueda de documentos",
        "publication_year": 2021,
        "authorships": [
            {"author": {"display_name": "Ana Pérez"}},
            {"author": {"display_name": "Bruno López"}},
        ],
        "primary_location": {
            "landing_page_url": "https://ejemplo.com/articulo",
            "source": {"display_name": "Revista Ejemplar"},
        },
        "best_oa_location": {"pdf_url": "https://ejemplo.com/articulo.pdf"},
        "type": "article",
    }


class TestExtractUrl:
    def test_usa_landing_primary(self):
        papel = {"primary_location": {"landing_page_url": "https://a.com"}, "best_oa_location": {"landing_page_url": "https://b.com"}}
        assert _extract_url(papel) == "https://a.com"

    def test_cae_a_la_segunda_ubicacion(self):
        papel = {"primary_location": {}, "best_oa_location": {"landing_page_url": "https://b.com"}}
        assert _extract_url(papel) == "https://b.com"

    def test_cae_a_pdf(self):
        papel = {"primary_location": {"pdf_url": "https://a.com/x.pdf"}, "best_oa_location": {}}
        assert _extract_url(papel) == "https://a.com/x.pdf"

    def test_cae_al_doi(self):
        papel = {"primary_location": {}, "best_oa_location": {}, "ids": {"doi": "https://doi.org/x"}}
        assert _extract_url(papel) == "https://doi.org/x"

    def test_sin_url_devuelve_none(self):
        assert _extract_url({"primary_location": {}}) is None
        assert _extract_url(None) is None
        assert _extract_url([]) is None


class TestExtractAuthors:
    def test_extrae_nombres(self):
        papel = {
            "authorships": [
                {"author": {"display_name": "Ana"}},
                {"author": {}},
                {"foo": "bar"},
            ]
        }
        assert _extract_authors(papel) == ["Ana"]

    def test_sin_autores_devuelve_fallback(self):
        assert _extract_authors({"authorships": []}) == ["Autor no disponible"]
        assert _extract_authors({}) == ["Autor no disponible"]
        assert _extract_authors(None) == ["Autor no disponible"]

    def test_ignora_authorships_no_diccionarios(self):
        assert _extract_authors({"authorships": ["basura", {"author": {"display_name": "Ana"}}]}) == ["Ana"]


class TestSearchDocuments:
    def test_mapea_resultados(self, monkeypatch):
        papel = papel_razonable()
        cliente = FakeClient({"results": [papel]})
        monkeypatch.setattr("app.services.document_service.client", cliente)

        documentos = run(search_documents("calculo"))
        assert documentos == [
            {
                "title": "Búsqueda de documentos",
                "authors": ["Ana Pérez", "Bruno López"],
                "year": 2021,
                "url": "https://ejemplo.com/articulo",
                "type": "article",
                "source": "Revista Ejemplar",
            }
        ]

    def test_pide_8_resultados(self, monkeypatch):
        cliente = FakeClient({"results": []})
        monkeypatch.setattr("app.services.document_service.client", cliente)
        run(search_documents("calculo"))
        assert cliente.per_page == 8

    def test_titulo_fallback_cuando_no_hay_nombre(self, monkeypatch):
        cliente = FakeClient({"results": [{"display_name": None, "authorships": []}]})
        monkeypatch.setattr("app.services.document_service.client", cliente)
        documentos = run(search_documents("algo"))
        assert documentos[0]["title"] == "Sin título"
        assert documentos[0]["authors"] == ["Autor no disponible"]

    def test_respuesta_no_diccionario_devuelve_vacio(self, monkeypatch):
        monkeypatch.setattr("app.services.document_service.client", FakeClient(["a", "b"]))
        assert run(search_documents("algo")) == []

    def test_sin_resultados_devuelve_vacio(self, monkeypatch):
        monkeypatch.setattr("app.services.document_service.client", FakeClient({"results": []}))
        assert run(search_documents("algo")) == []

    def test_descarta_papers_no_diccionarios(self, monkeypatch):
        cliente = FakeClient({"results": ["basura", papel_razonable()]})
        monkeypatch.setattr("app.services.document_service.client", cliente)
        assert len(run(search_documents("algo"))) == 1


class TestSearchEndpoint:
    def test_sin_parametro_devuelve_422(self, client):
        assert client.get("/search").status_code == 422

    def test_consulta_vacia_devuelve_400(self, client):
        respuesta = client.get("/search", params={"q": ""})
        assert respuesta.status_code == 400

    def test_consulta_en_blanco_devuelve_400(self, client):
        respuesta = client.get("/search", params={"q": "   "})
        assert respuesta.status_code == 400

    def test_no_requiere_token(self, client, monkeypatch):
        cliente = FakeClient({"results": [papel_razonable()]})
        monkeypatch.setattr("app.services.document_service.client", cliente)
        respuesta = client.get("/search", params={"q": "calculo"})
        assert respuesta.status_code == 200
        assert respuesta.json()[0]["title"] == "Búsqueda de documentos"
