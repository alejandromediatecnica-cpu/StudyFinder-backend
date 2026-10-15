import time

import app.database as database

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n%prueba\n%%EOF"


def subir_pdf(client, headers, nombre="tarea.pdf", contenido=PDF_BYTES, tipo="application/pdf"):
    return client.post(
        "/documents",
        headers=headers,
        files={"file": (nombre, contenido, tipo)},
    )


def registrar_usuario(client, email, nombre="Bruno", password="secreta2"):
    respuesta = client.post("/auth/signup", json={"name": nombre, "email": email, "password": password})
    assert respuesta.status_code == 201
    return {
        "token": respuesta.json()["token"],
        "headers": {"Authorization": f"Bearer {respuesta.json()['token']}"},
        "id": respuesta.json()["user"]["id"],
    }


class TestAuth:
    def test_get_documents_sin_token(self, client):
        assert client.get("/documents").status_code == 401

    def test_get_documents_con_token_invalido(self, client):
        respuesta = client.get("/documents", headers={"Authorization": "Bearer falso"})
        assert respuesta.status_code == 401

    def test_upload_sin_token(self, client):
        respuesta = client.post(
            "/documents", files={"file": ("tarea.pdf", PDF_BYTES, "application/pdf")}
        )
        assert respuesta.status_code == 401


class TestUpload:
    def test_extension_no_permitida(self, client, user):
        respuesta = subir_pdf(client, user["headers"], nombre="apuntes.txt")
        assert respuesta.status_code == 400
        assert respuesta.json()["detail"] == "Tipo de archivo no permitido."

    def test_archivo_sin_nombre(self, client, user):
        respuesta = client.post(
            "/documents", headers=user["headers"], files={"file": ("   ", PDF_BYTES, "application/pdf")}
        )
        assert respuesta.status_code == 400

    def test_archivo_vacio(self, client, user):
        respuesta = subir_pdf(client, user["headers"], contenido=b"")
        assert respuesta.status_code == 400
        assert respuesta.json()["detail"] == "El archivo está vacío."

    def test_archivo_mayor_a_25_mb(self, client, user):
        grande = b"x" * (25 * 1024 * 1024 + 1)
        respuesta = subir_pdf(client, user["headers"], contenido=grande)
        assert respuesta.status_code == 413

    def test_supera_la_cuota_de_3_mb_del_usuario(self, client, user):
        casi_lleno = b"x" * (3 * 1024 * 1024 - 1)
        subir_pdf(client, user["headers"], contenido=casi_lleno)

        respuesta = subir_pdf(client, user["headers"], nombre="otros.pdf")
        assert respuesta.status_code == 413
        assert "3 MB" in respuesta.json()["detail"]

        # El usuario sigue pudiendo gestionar sus documentos.
        assert len(client.get("/documents", headers=user["headers"]).json()) == 1

    def test_subida_correcta_guarda_el_archivo(self, client, user):
        respuesta = subir_pdf(client, user["headers"])
        assert respuesta.status_code == 201
        datos = respuesta.json()
        assert datos["id"]
        assert datos["filename"] == "tarea.pdf"
        assert datos["contentType"] == "application/pdf"
        assert datos["size"] == len(PDF_BYTES)
        assert datos["createdAt"]

        archivos = [
            p for p in database.UPLOADS_PATH.rglob("*")
            if p.is_file() and p.suffix == ".pdf"
        ]
        assert len(archivos) == 1
        assert archivos[0].read_bytes() == PDF_BYTES

    def test_extension_en_mayusculas_se_normaliza(self, client, user):
        respuesta = subir_pdf(client, user["headers"], nombre="TAREA.PDF")
        assert respuesta.status_code == 201


class TestList:
    def test_listar_documentos_del_propietario(self, client, user):
        primero = subir_pdf(client, user["headers"], nombre="a.pdf").json()
        segundo = subir_pdf(client, user["headers"], nombre="b.pdf").json()

        respuesta = client.get("/documents", headers=user["headers"])
        assert respuesta.status_code == 200
        ids = [d["id"] for d in respuesta.json()]
        assert ids.index(segundo["id"]) < ids.index(primero["id"])

    def test_documentos_aislados_por_usuario(self, client, user):
        otro = registrar_usuario(client, "bruno@correo.com")
        subir_pdf(client, user["headers"])
        subir_pdf(client, otro["headers"], nombre="bruno.pdf")

        del_ana = client.get("/documents", headers=user["headers"]).json()
        del_bruno = client.get("/documents", headers=otro["headers"]).json()

        assert [d["filename"] for d in del_ana] == ["tarea.pdf"]
        assert [d["filename"] for d in del_bruno] == ["bruno.pdf"]

    def test_listar_por_materia(self, client, user):
        subir_pdf(client, user["headers"])
        respuesta = client.get("/documents", headers=user["headers"], params={"materia": "Cálculo"})
        assert respuesta.status_code == 200
        assert respuesta.json() == []


class TestListadoPublico:
    def test_listado_publico_sin_token(self, client):
        assert client.get("/documents/public").status_code == 401

    def test_muestra_solo_los_documentos_de_otros(self, client, user):
        subir_pdf(client, user["headers"], nombre="propio.pdf")
        otro = registrar_usuario(client, "bruno@correo.com", nombre="Bruno")
        subir_pdf(client, otro["headers"], nombre="bruno.pdf")

        publicos = client.get("/documents/public", headers=user["headers"]).json()
        assert [d["filename"] for d in publicos] == ["bruno.pdf"]
        assert publicos[0]["ownerName"] == "Bruno"
        assert publicos[0]["ownerEmail"] == "bruno@correo.com"

    def test_listado_es_simetrico_entre_usuarios(self, client, user):
        subir_pdf(client, user["headers"])
        otro = registrar_usuario(client, "bruno@correo.com")
        subir_pdf(client, otro["headers"], nombre="bruno.pdf")

        del_ana = [d["filename"] for d in client.get("/documents/public", headers=user["headers"]).json()]
        del_bruno = [d["filename"] for d in client.get("/documents/public", headers=otro["headers"]).json()]

        assert del_ana == ["bruno.pdf"]
        assert del_bruno == ["tarea.pdf"]


class TestDownload:
    def test_descarga_devuelve_contenido(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        respuesta = client.get(f"/documents/{documento['id']}/download", headers=user["headers"])
        assert respuesta.status_code == 200
        assert respuesta.content == PDF_BYTES

    def test_descarga_documento_inexistente(self, client, user):
        respuesta = client.get("/documents/no-existe/download", headers=user["headers"])
        assert respuesta.status_code == 404

    def test_se_puede_descargar_documento_de_otro_usuario(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        otro = registrar_usuario(client, "bruno@correo.com")
        respuesta = client.get(f"/documents/{documento['id']}/download", headers=otro["headers"])
        assert respuesta.status_code == 200
        assert respuesta.content == PDF_BYTES

    def test_descarga_se_gestiona_sin_fuera_de_uploads(self, client, user):
        documento = database.create_document(
            user["user"]["id"], "malicioso.pdf", "../secreto.pdf", "application/pdf", 5
        )
        fuera = database.UPLOADS_PATH.parent / "secreto.pdf"
        fuera.write_bytes(b"contenido secreto")
        try:
            respuesta = client.get(f"/documents/{documento['id']}/download", headers=user["headers"])
            assert respuesta.status_code == 404
        finally:
            fuera.unlink(missing_ok=True)


class TestMaterias:
    def test_asignar_materia_devuelve_204(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        respuesta = client.post(
            f"/documents/{documento['id']}/materias",
            json={"materia": "Cálculo"},
            headers=user["headers"],
        )
        assert respuesta.status_code == 204

    def test_asignar_materia_a_documento_inexistente(self, client, user):
        respuesta = client.post(
            "/documents/no-existe/materias", json={"materia": "Cálculo"}, headers=user["headers"]
        )
        assert respuesta.status_code == 404

    def test_se_puede_asignar_a_materia_un_documento_ajeno(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        otro = registrar_usuario(client, "bruno@correo.com")

        respuesta = client.post(
            f"/documents/{documento['id']}/materias",
            json={"materia": "Cálculo"},
            headers=otro["headers"],
        )
        assert respuesta.status_code == 204
        en_materia = client.get("/documents", headers=otro["headers"], params={"materia": "Cálculo"})
        assert [d["id"] for d in en_materia.json()] == [documento["id"]]

    def test_el_documento_aparece_filtrado_por_materia(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        client.post(
            f"/documents/{documento['id']}/materias",
            json={"materia": "Cálculo"},
            headers=user["headers"],
        )

        en_materia = client.get("/documents", headers=user["headers"], params={"materia": "Cálculo"})
        assert [d["id"] for d in en_materia.json()] == [documento["id"]]

        sin_materia = client.get("/documents", headers=user["headers"], params={"materia": "Física"})
        assert sin_materia.json() == []

    def test_quitar_materia(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        client.post(
            f"/documents/{documento['id']}/materias",
            json={"materia": "Cálculo"},
            headers=user["headers"],
        )
        respuesta = client.delete(
            f"/documents/{documento['id']}/materias", params={"materia": "Cálculo"}, headers=user["headers"]
        )
        assert respuesta.status_code == 204

        filtrado = client.get("/documents", headers=user["headers"], params={"materia": "Cálculo"})
        assert filtrado.json() == []

    def test_quitar_materia_sin_token(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        respuesta = client.delete(f"/documents/{documento['id']}/materias", params={"materia": "Cálculo"})
        assert respuesta.status_code == 401


class TestDelete:
    def test_eliminar_documento_borra_archivo(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        respuesta = client.delete(f"/documents/{documento['id']}", headers=user["headers"])
        assert respuesta.status_code == 204

        assert client.get("/documents", headers=user["headers"]).json() == []
        assert list(database.UPLOADS_PATH.rglob("*.pdf")) == []

    def test_eliminar_documento_inexistente(self, client, user):
        respuesta = client.delete("/documents/no-existe", headers=user["headers"])
        assert respuesta.status_code == 404

    def test_no_elimina_documento_de_otro_usuario(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        otro = registrar_usuario(client, "bruno@correo.com")
        respuesta = client.delete(f"/documents/{documento['id']}", headers=otro["headers"])
        assert respuesta.status_code == 404

    def test_eliminar_sin_token(self, client, user):
        documento = subir_pdf(client, user["headers"]).json()
        respuesta = client.delete(f"/documents/{documento['id']}")
        assert respuesta.status_code == 401


def test_flujo_completo_upload_materia_descarga_elimina(client, user):
    documento = subir_pdf(client, user["headers"]).json()
    client.post(
        f"/documents/{documento['id']}/materias",
        json={"materia": "Cálculo"},
        headers=user["headers"],
    )

    en_materia = client.get("/documents", headers=user["headers"], params={"materia": "Cálculo"})
    assert [d["id"] for d in en_materia.json()] == [documento["id"]]

    archivo = client.get(f"/documents/{documento['id']}/download", headers=user["headers"])
    assert archivo.content == PDF_BYTES

    time.sleep(0.01)
    assert client.delete(f"/documents/{documento['id']}", headers=user["headers"]).status_code == 204
    assert client.get("/documents", headers=user["headers"]).json() == []