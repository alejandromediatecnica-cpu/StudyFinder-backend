def crear(client, headers, **cambios):
    cuerpo = {"title": "Estudiar cálculo", "subject": "Cálculo", "dueDate": "2026-09-10"}
    cuerpo.update(cambios)
    return client.post("/tasks", json=cuerpo, headers=headers)


class TestTareas:
    def test_lista_vacia(self, client, user):
        assert client.get("/tasks", headers=user["headers"]).json() == []

    def test_sin_token(self, client):
        assert client.get("/tasks").status_code == 401

    def test_crear_tarea(self, client, user):
        respuesta = crear(client, user["headers"])
        assert respuesta.status_code == 201
        tarea = respuesta.json()
        assert tarea["title"] == "Estudiar cálculo"
        assert tarea["subject"] == "Cálculo"
        assert tarea["dueDate"] == "2026-09-10"
        assert tarea["completed"] is False
        assert tarea["id"]

    def test_crear_sin_titulo(self, client, user):
        respuesta = client.post("/tasks", json={"title": "   "}, headers=user["headers"])
        assert respuesta.status_code == 422

    def test_actualizar_tarea(self, client, user):
        tarea = crear(client, user["headers"]).json()
        respuesta = client.patch(
            f"/tasks/{tarea['id']}",
            json={"title": "Nuevo título", "completed": True, "status": "done"},
            headers=user["headers"],
        )
        assert respuesta.status_code == 200
        actualizada = respuesta.json()
        assert actualizada["title"] == "Nuevo título"
        assert actualizada["completed"] is True
        assert actualizada["status"] == "done"

    def test_actualizar_tarea_inexistente(self, client, user):
        respuesta = client.patch(
            "/tasks/no-existe", json={"title": "x"}, headers=user["headers"]
        )
        assert respuesta.status_code == 404

    def test_eliminar_tarea(self, client, user):
        tarea = crear(client, user["headers"]).json()
        assert client.delete(f"/tasks/{tarea['id']}", headers=user["headers"]).status_code == 204
        assert client.get("/tasks", headers=user["headers"]).json() == []

    def test_eliminar_tarea_inexistente(self, client, user):
        assert client.delete("/tasks/no-existe", headers=user["headers"]).status_code == 404

    def test_tareas_aisladas_por_usuario(self, client, user):
        crear(client, user["headers"])
        otro = client.post(
            "/auth/signup",
            json={"name": "Bruno", "email": "bruno@correo.com", "password": "ClaveSegura2026x"},
        ).json()
        headers = {"Authorization": f"Bearer {otro['token']}"}
        assert client.get("/tasks", headers=headers).json() == []

    def test_no_modifica_ni_borra_tarea_ajena(self, client, user):
        tarea = crear(client, user["headers"]).json()
        otro = client.post(
            "/auth/signup",
            json={"name": "Bruno", "email": "bruno@correo.com", "password": "ClaveSegura2026x"},
        ).json()
        headers = {"Authorization": f"Bearer {otro['token']}"}
        assert client.patch(f"/tasks/{tarea['id']}", json={"title": "x"}, headers=headers).status_code == 404
        assert client.delete(f"/tasks/{tarea['id']}", headers=headers).status_code == 404

    def test_guarda_documentos_adjuntos(self, client, user):
        documentos = [{"id": "d1", "name": "Apuntes.pdf", "origin": "biblioteca"}]
        tarea = crear(client, user["headers"], documents=documentos).json()
        assert tarea["documents"] == documentos

    def test_eliminar_todas_las_tareas(self, client, user):
        for i in range(40):
            crear(client, user["headers"], title=f"Tarea {i}")
        assert len(client.get("/tasks", headers=user["headers"]).json()) == 40

        assert client.delete("/tasks", headers=user["headers"]).status_code == 204
        assert client.get("/tasks", headers=user["headers"]).json() == []

    def test_eliminar_todas_tambien_borra_eventos(self, client, user):
        crear(client, user["headers"])
        client.post(
            "/calendar",
            json={"title": "Examen", "date": "2026-09-15"},
            headers=user["headers"],
        )
        assert client.delete("/tasks", headers=user["headers"]).status_code == 204
        assert client.get("/tasks", headers=user["headers"]).json() == []
        assert client.get("/calendar", headers=user["headers"]).json() == []

    def test_eliminar_todas_sin_token(self, client):
        assert client.delete("/tasks").status_code == 401

    def test_eliminar_todas_no_afecta_a_otros(self, client, user):
        crear(client, user["headers"])
        otro = client.post(
            "/auth/signup",
            json={"name": "Bruno", "email": "bruno@correo.com", "password": "ClaveSegura2026x"},
        ).json()
        headers = {"Authorization": f"Bearer {otro['token']}"}
        crear(client, headers, title="De Bruno")

        client.delete("/tasks", headers=user["headers"])
        assert len(client.get("/tasks", headers=headers).json()) == 1


class TestCalendario:
    def test_lista_vacia(self, client, user):
        assert client.get("/calendar", headers=user["headers"]).json() == []

    def test_crear_evento(self, client, user):
        respuesta = client.post(
            "/calendar",
            json={"title": "Examen", "date": "2026-09-15", "hour": "10:00", "type": "evento"},
            headers=user["headers"],
        )
        assert respuesta.status_code == 201
        evento = respuesta.json()
        assert evento["title"] == "Examen"
        assert evento["date"] == "2026-09-15"
        assert evento["hour"] == "10:00"

    def test_crear_sin_titulo(self, client, user):
        respuesta = client.post(
            "/calendar", json={"title": "  ", "date": "2026-09-15"}, headers=user["headers"]
        )
        assert respuesta.status_code == 422

    def test_actualizar_evento(self, client, user):
        evento = client.post(
            "/calendar",
            json={"title": "Examen", "date": "2026-09-15"},
            headers=user["headers"],
        ).json()
        respuesta = client.patch(
            f"/calendar/{evento['id']}",
            json={"title": "Examen final", "date": "2026-09-16"},
            headers=user["headers"],
        )
        assert respuesta.status_code == 200
        assert respuesta.json()["title"] == "Examen final"
        assert respuesta.json()["date"] == "2026-09-16"

    def test_eliminar_evento(self, client, user):
        evento = client.post(
            "/calendar",
            json={"title": "Examen", "date": "2026-09-15"},
            headers=user["headers"],
        ).json()
        assert client.delete(f"/calendar/{evento['id']}", headers=user["headers"]).status_code == 204
        assert client.get("/calendar", headers=user["headers"]).json() == []

    def test_eventos_aislados_por_usuario(self, client, user):
        client.post(
            "/calendar",
            json={"title": "Examen", "date": "2026-09-15"},
            headers=user["headers"],
        )
        otro = client.post(
            "/auth/signup",
            json={"name": "Bruno", "email": "bruno@correo.com", "password": "ClaveSegura2026x"},
        ).json()
        headers = {"Authorization": f"Bearer {otro['token']}"}
        assert client.get("/calendar", headers=headers).json() == []
