import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.clients.document_client import DocumentClient


def run(coro):
    return asyncio.run(coro)


# Se captura el AsyncClient real antes de que los tests lo reemplacen.
_ASYNC_CLIENT_REAL = httpx.AsyncClient


def cliente_con_transporte(monkeypatch, handler):
    import app.clients.document_client as modulo

    def fabrica(**kwargs):
        timeout = kwargs.get("timeout", 20.0)
        return _ASYNC_CLIENT_REAL(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(modulo.httpx, "AsyncClient", fabrica)
    return DocumentClient()


def request():
    return httpx.Request("GET", "https://api.openalex.org/works")


class TestParametros:
    def test_busca_en_openalex_con_terminos_and(self, monkeypatch):
        visto = {}

        async def handler(request):
            visto["url"] = str(request.url)
            visto["search"] = request.url.params["search"]
            visto["per_page"] = request.url.params["per_page"]
            visto["ua"] = request.headers.get("user-agent")
            return httpx.Response(200, json={"results": []})

        cliente = cliente_con_transporte(monkeypatch, handler)
        resultado = run(cliente.search_documents("calculo vectorial", per_page=8))

        assert resultado == {"results": []}
        assert "/works" in visto["url"]
        assert visto["search"] == "calculo AND vectorial"
        assert visto["per_page"] == "8"
        assert "StudyFinderApp" in visto["ua"]

    def test_limpia_comillas_de_la_consulta(self, monkeypatch):
        visto = {}

        async def handler(request):
            visto["search"] = request.url.params["search"]
            return httpx.Response(200, json={"results": []})

        cliente = cliente_con_transporte(monkeypatch, handler)
        run(cliente.search_documents('el "término" \'exacto\'', per_page=8))
        assert visto["search"] == "el AND término AND exacto"

    def test_filtra_solo_textos_completos(self, monkeypatch):
        visto = {}

        async def handler(request):
            visto["filter"] = request.url.params["filter"]
            return httpx.Response(200, json={"results": []})

        cliente = cliente_con_transporte(monkeypatch, handler)
        run(cliente.search_documents("calculo", per_page=8))
        assert visto["filter"] == "has_fulltext:true"


class TestValidacionConsulta:
    @pytest.mark.parametrize("consulta", ["", "   ", None])
    def test_consulta_invalida_rechaza(self, monkeypatch, consulta):
        cliente = cliente_con_transporte(monkeypatch, lambda request: httpx.Response(200, json={}))

        with pytest.raises(HTTPException) as exc:
            run(cliente.search_documents(consulta))
        assert exc.value.status_code == 400


class TestErroresOpenAlex:
    def test_connect_error_devuelve_503(self, monkeypatch):
        async def handler(request):
            raise httpx.ConnectError("sin conexion", request=request)

        cliente = cliente_con_transporte(monkeypatch, handler)
        with pytest.raises(HTTPException) as exc:
            run(cliente.search_documents("calculo", per_page=8))
        assert exc.value.status_code == 503

    def test_timeout_devuelve_504(self, monkeypatch):
        async def handler(request):
            raise httpx.TimeoutException("muy lenta", request=request)

        cliente = cliente_con_transporte(monkeypatch, handler)
        with pytest.raises(HTTPException) as exc:
            run(cliente.search_documents("calculo", per_page=8))
        assert exc.value.status_code == 504

    def test_error_http_transmite_el_estado(self, monkeypatch):
        async def handler(request):
            return httpx.Response(500, json={"error": "boom"}, request=request)

        cliente = cliente_con_transporte(monkeypatch, handler)
        with pytest.raises(HTTPException) as exc:
            run(cliente.search_documents("calculo", per_page=8))
        assert exc.value.status_code == 500
        assert "OpenAlex respondió con error" in exc.value.detail

    def test_error_de_red_generico_devuelve_503(self, monkeypatch):
        async def handler(request):
            raise httpx.NetworkError("dns roto", request=request)

        cliente = cliente_con_transporte(monkeypatch, handler)
        with pytest.raises(HTTPException) as exc:
            run(cliente.search_documents("calculo", per_page=8))
        assert exc.value.status_code == 503