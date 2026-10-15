import importlib

import app.config as config


def test_cors_origins_por_defecto(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    try:
        recargado = importlib.reload(config)
        assert recargado.CORS_ORIGINS == ["http://localhost:4200", "http://127.0.0.1:4200"]
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_cors_origins_desde_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " https://studyfinder.app , ,https://www.studyfinder.app ")
    try:
        recargado = importlib.reload(config)
        assert recargado.CORS_ORIGINS == ["https://studyfinder.app", "https://www.studyfinder.app"]
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_cors_permite_origen_del_frontend(client):
    respuesta = client.options(
        "/",
        headers={"Origin": "http://localhost:4200", "Access-Control-Request-Method": "GET"},
    )
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:4200"


def test_cors_rechaza_origen_desconocido(client):
    respuesta = client.get("/", headers={"Origin": "https://malicioso.example"})
    assert "access-control-allow-origin" not in respuesta.headers
