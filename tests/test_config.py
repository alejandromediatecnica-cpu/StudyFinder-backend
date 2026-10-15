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


def test_uploads_dir_por_defecto_en_la_raiz_del_backend(monkeypatch):
    monkeypatch.delenv("UPLOADS_DIR", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    try:
        recargado = importlib.reload(config)
        assert recargado.UPLOADS_DIR == recargado.BACKEND_ROOT / "uploads"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_uploads_dir_desde_variable_de_entorno(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "subidas"))
    try:
        recargado = importlib.reload(config)
        assert recargado.UPLOADS_DIR == tmp_path / "subidas"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_inicializar_no_escribe_en_disco(db, monkeypatch, tmp_path):
    # Vercel monta el código en un sistema de archivos de solo lectura: arrancar
    # la app no puede crear la carpeta de subidas.
    def prohibido(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(db, "UPLOADS_PATH", tmp_path / "solo-lectura" / "uploads")
    monkeypatch.setattr("pathlib.Path.mkdir", prohibido)
    db.initialize_database()
    assert not (tmp_path / "solo-lectura").exists()


def test_cors_permite_origen_del_frontend(client):
    respuesta = client.options(
        "/",
        headers={"Origin": "http://localhost:4200", "Access-Control-Request-Method": "GET"},
    )
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:4200"


def test_cors_rechaza_origen_desconocido(client):
    respuesta = client.get("/", headers={"Origin": "https://malicioso.example"})
    assert "access-control-allow-origin" not in respuesta.headers
