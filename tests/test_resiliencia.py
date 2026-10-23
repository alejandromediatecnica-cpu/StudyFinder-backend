from pymongo.errors import ServerSelectionTimeoutError

import app.database as database


def caida(*args, **kwargs):
    raise ServerSelectionTimeoutError("cluster.example.net:27017: timed out")


def test_inicializar_no_falla_si_mongo_no_responde(db, monkeypatch, caplog):
    monkeypatch.setattr(database.users_collection, "create_index", caida)
    database.initialize_database()
    assert "No se pudo inicializar MongoDB" in caplog.text


def test_raiz_responde_aunque_mongo_no_este_disponible(client, monkeypatch):
    monkeypatch.setattr(database.users_collection, "create_index", caida)
    assert client.get("/").status_code == 200


def test_ruta_con_datos_devuelve_503_con_cors_si_mongo_cae(client, monkeypatch):
    monkeypatch.setattr(database.users_collection, "find_one", caida)
    respuesta = client.post(
        "/auth/login",
        json={"email": "ana@correo.com", "password": "ClaveSegura2026x"},
        headers={"Origin": "http://localhost:4200"},
    )
    assert respuesta.status_code == 503
    assert respuesta.json() == {
        "detail": "La base de datos no está disponible. Inténtalo de nuevo en unos minutos."
    }
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:4200"


def test_cliente_mongo_con_timeout_corto(monkeypatch):
    capturado = {}

    class ClienteFalso:
        def __init__(self, url, **opciones):
            capturado.update(opciones)

    monkeypatch.setattr(database, "_client", None)
    monkeypatch.setattr(database, "MongoClient", ClienteFalso)
    database.get_client()
    assert capturado["serverSelectionTimeoutMS"] == 10_000
