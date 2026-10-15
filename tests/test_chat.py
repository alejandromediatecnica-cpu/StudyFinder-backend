from app.api import messages as messages_api


def registrar(client, email, nombre="Bruno"):
    respuesta = client.post(
        "/auth/signup",
        json={"name": nombre, "email": email, "password": "secreta2"},
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    return {
        "token": datos["token"],
        "headers": {"Authorization": f"Bearer {datos['token']}"},
        "id": datos["user"]["id"],
    }


def id_de(usuario):
    return usuario.get("id") or usuario["user"]["id"]


def hacerse_contactos(client, a, b):
    client.post("/contacts/requests", json={"usuario_id": id_de(b)}, headers=a["headers"])
    pendientes = client.get("/contacts/requests/incoming", headers=b["headers"]).json()
    client.post(f"/contacts/requests/{pendientes[0]['id']}/accept", headers=b["headers"])


class TestPerfil:
    def test_me_devuelve_perfil(self, client, user):
        respuesta = client.get("/users/me", headers=user["headers"])
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["email"] == user["email"]
        assert cuerpo["name"] == "Ana"
        assert "codigo" not in cuerpo

    def test_me_sin_token(self, client):
        assert client.get("/users/me").status_code == 401

    def test_usuario_legacy_con_id_entero(self, client, db):
        # La migracion SQLite dejo usuarios con _id entero y sesiones con el
        # user_id como string; antes esto rompia get_current_user con InvalidId.
        db.users_collection.insert_one({"_id": 99, "name": "Legacy", "email": "legacy@correo.com"})
        db.sessions_collection.insert_one({"token": "tok-legacy", "user_id": "99"})

        respuesta = client.get("/users/me", headers={"Authorization": "Bearer tok-legacy"})
        assert respuesta.status_code == 200
        assert respuesta.json()["name"] == "Legacy"


class TestBusqueda:
    def test_busca_por_nombre_parcial(self, client, user):
        registrar(client, "bruno@correo.com", nombre="Bruno Diaz")
        resultados = client.get("/users/search", params={"nombre": "bruno"}, headers=user["headers"]).json()
        assert len(resultados) == 1
        assert resultados[0]["name"] == "Bruno Diaz"
        assert "id" in resultados[0]

    def test_busqueda_insensible_a_mayusculas(self, client, user):
        registrar(client, "bruno@correo.com", nombre="Bruno")
        resultados = client.get("/users/search", params={"nombre": "BRUNO"}, headers=user["headers"]).json()
        assert len(resultados) == 1

    def test_busqueda_excluye_a_si_mismo(self, client, user):
        resultados = client.get("/users/search", params={"nombre": "Ana"}, headers=user["headers"]).json()
        assert resultados == []

    def test_busqueda_muy_corta(self, client, user):
        assert client.get("/users/search", params={"nombre": "b"}, headers=user["headers"]).json() == []

    def test_busqueda_sin_token(self, client):
        assert client.get("/users/search", params={"nombre": "Bruno"}).status_code == 401


class TestSolicitudes:
    def test_enviar_solicitud_por_id(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        respuesta = client.post(
            "/contacts/requests", json={"usuario_id": otro["id"]}, headers=user["headers"]
        )
        assert respuesta.status_code == 200
        entrantes = client.get("/contacts/requests/incoming", headers=otro["headers"]).json()
        assert len(entrantes) == 1
        assert entrantes[0]["emisor"]["name"] == user["user"]["name"]

    def test_usuario_desconocido(self, client, user):
        respuesta = client.post(
            "/contacts/requests", json={"usuario_id": "000000000000000000000000"}, headers=user["headers"]
        )
        assert respuesta.status_code == 404

    def test_no_puede_agregarse_a_si_mismo(self, client, user):
        respuesta = client.post(
            "/contacts/requests", json={"usuario_id": user["user"]["id"]}, headers=user["headers"]
        )
        assert respuesta.status_code == 400

    def test_solicitud_duplicada(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        assert client.post(
            "/contacts/requests", json={"usuario_id": otro["id"]}, headers=user["headers"]
        ).status_code == 200
        respuesta = client.post(
            "/contacts/requests", json={"usuario_id": otro["id"]}, headers=user["headers"]
        )
        assert respuesta.status_code == 409

    def test_salientes(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        client.post("/contacts/requests", json={"usuario_id": otro["id"]}, headers=user["headers"])
        salientes = client.get("/contacts/requests/outgoing", headers=user["headers"]).json()
        assert len(salientes) == 1
        assert salientes[0]["receptor"]["name"] == "Bruno"

    def test_aceptar_convierte_en_contacto(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        hacerse_contactos(client, user, otro)

        contactos = client.get("/contacts/", headers=user["headers"]).json()
        assert len(contactos) == 1
        assert contactos[0]["otro"]["name"] == "Bruno"

    def test_solo_el_receptor_acepta(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        client.post("/contacts/requests", json={"usuario_id": otro["id"]}, headers=user["headers"])
        pendientes = client.get("/contacts/requests/incoming", headers=otro["headers"]).json()
        respuesta = client.post(
            f"/contacts/requests/{pendientes[0]['id']}/accept", headers=user["headers"]
        )
        assert respuesta.status_code == 403

    def test_rechazar_no_crea_contacto(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        client.post("/contacts/requests", json={"usuario_id": otro["id"]}, headers=user["headers"])
        pendientes = client.get("/contacts/requests/incoming", headers=otro["headers"]).json()
        assert client.post(
            f"/contacts/requests/{pendientes[0]['id']}/reject", headers=otro["headers"]
        ).status_code == 200
        assert client.get("/contacts/", headers=user["headers"]).json() == []


class TestMensajes:
    def test_sin_ser_contacto_no_puede_enviar(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        respuesta = client.post(
            f"/messages/{otro['id']}", data={"texto": "hola"}, headers=user["headers"]
        )
        assert respuesta.status_code == 403

    def test_enviar_y_listar(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        hacerse_contactos(client, user, otro)

        envio = client.post(f"/messages/{otro['id']}", data={"texto": "hola"}, headers=user["headers"])
        assert envio.status_code == 201
        assert envio.json()["texto"] == "hola"

        listado = client.get(f"/messages/{otro['id']}", headers=user["headers"]).json()
        assert [m["texto"] for m in listado] == ["hola"]
        # El receptor ve el mismo historial.
        assert client.get(f"/messages/{user['user']['id']}", headers=otro["headers"]).json()[0]["texto"] == "hola"

    def test_historial_ordenado_y_paginado(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        hacerse_contactos(client, user, otro)
        for i in range(3):
            client.post(f"/messages/{otro['id']}", data={"texto": f"m{i}"}, headers=user["headers"])

        listado = client.get(f"/messages/{otro['id']}", headers=user["headers"]).json()
        assert [m["texto"] for m in listado] == ["m0", "m1", "m2"]
        limitado = client.get(f"/messages/{otro['id']}?limit=2", headers=user["headers"]).json()
        assert [m["texto"] for m in limitado] == ["m1", "m2"]

    def test_mensaje_vacio(self, client, user):
        otro = registrar(client, "bruno@correo.com")
        hacerse_contactos(client, user, otro)
        respuesta = client.post(f"/messages/{otro['id']}", data={}, headers=user["headers"])
        assert respuesta.status_code == 400

    def test_envia_archivo(self, client, user, monkeypatch, tmp_path):
        uploads = tmp_path / "uploads"
        monkeypatch.setattr(messages_api, "UPLOADS_PATH", uploads)
        otro = registrar(client, "bruno@correo.com")
        hacerse_contactos(client, user, otro)

        respuesta = client.post(
            f"/messages/{otro['id']}",
            data={"texto": ""},
            files={"archivo": ("nota.pdf", b"contenido", "application/pdf")},
            headers=user["headers"],
        )
        assert respuesta.status_code == 201
        cuerpo = respuesta.json()
        assert cuerpo["nombre_archivo"] == "nota.pdf"
        assert cuerpo["tamano_archivo"] == len(b"contenido")

    def test_archivo_demasiado_grande(self, client, user, monkeypatch, tmp_path):
        uploads = tmp_path / "uploads"
        monkeypatch.setattr(messages_api, "UPLOADS_PATH", uploads)
        otro = registrar(client, "bruno@correo.com")
        hacerse_contactos(client, user, otro)

        grande = b"x" * (3 * 1024 * 1024 + 1)
        respuesta = client.post(
            f"/messages/{otro['id']}",
            files={"archivo": ("grande.pdf", grande, "application/pdf")},
            headers=user["headers"],
        )
        assert respuesta.status_code == 413
