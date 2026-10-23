from pymongo.errors import DuplicateKeyError

import pytest

import app.database as db


def test_insertar_y_buscar_usuario(db):
    resultado = db.users_collection.insert_one(
        {"name": "Ana", "email": "ana@correo.com", "password_hash": "h1", "password_salt": "s1"}
    )
    assert resultado.inserted_id is not None

    usuario = db.users_collection.find_one({"email": "ana@correo.com"})
    assert usuario is not None
    assert usuario["_id"] == resultado.inserted_id
    assert usuario["name"] == "Ana"
    assert usuario["email"] == "ana@correo.com"
    assert usuario["password_hash"] == "h1"
    assert usuario["password_salt"] == "s1"
    assert "id" not in usuario


def test_email_unico_en_usuarios(db):
    datos = {"name": "Ana", "email": "ana@correo.com", "password_hash": "h1", "password_salt": "s1"}
    db.users_collection.insert_one(datos)
    with pytest.raises(DuplicateKeyError):
        db.users_collection.insert_one({**datos, "name": "Otra"})


def test_normalize_name_key_ignora_mayusculas_acentos_y_espacios(db):
    assert db.normalize_name_key("  José   Pérez ") == "jose perez"
    assert db.normalize_name_key("JOSE PEREZ") == db.normalize_name_key("josé pérez")


def test_nombre_unico_en_usuarios(db):
    db.users_collection.insert_one(
        {
            "name": "Ana",
            "name_key": db.normalize_name_key("Ana"),
            "email": "ana@correo.com",
            "password_hash": "h1",
            "password_salt": "s1",
        }
    )
    with pytest.raises(DuplicateKeyError):
        db.users_collection.insert_one(
            {
                "name": "ana",
                "name_key": db.normalize_name_key("ANA"),
                "email": "otra@correo.com",
                "password_hash": "h1",
                "password_salt": "s1",
            }
        )


def test_buscar_usuario_desconocido(db):
    assert db.users_collection.find_one({"email": "nadie@correo.com"}) is None


def test_sesiones_crear_consultar_y_revocar(db):
    token = db.create_session("42")
    assert len(token) == 64
    assert db.user_id_for_token(token) == "42"

    db.revoke_session(token)
    assert db.user_id_for_token(token) is None


def test_sesion_inexistente(db):
    assert db.user_id_for_token("token-desconocido") is None


def test_crear_y_listar_documentos(db):
    documento = db.create_document("1", "tarea.pdf", "uploads/1/a.pdf", "application/pdf", 10)
    assert documento["id"]
    assert documento["user_id"] == "1"
    assert documento["filename"] == "tarea.pdf"
    assert documento["content_type"] == "application/pdf"
    assert documento["size"] == 10

    documentos = db.list_documents("1")
    assert [d["id"] for d in documentos] == [documento["id"]]
    assert db.list_documents("2") == []


def test_documentos_ordenados_por_fecha_descendente(db):
    primero = db.create_document("1", "a.pdf", "uploads/1/a.pdf", "application/pdf", 1)
    segundo = db.create_document("1", "b.pdf", "uploads/1/b.pdf", "application/pdf", 2)
    ids = [d["id"] for d in db.list_documents("1")]
    assert ids.index(segundo["id"]) < ids.index(primero["id"])


def test_obtener_documento_solo_del_propietario(db):
    documento = db.create_document("1", "a.pdf", "uploads/1/a.pdf", "application/pdf", 1)
    assert db.get_document("1", documento["id"]) is not None
    assert db.get_document("2", documento["id"]) is None
    assert db.get_document("1", "no-existe") is None


def test_asociar_materia_al_documento(db):
    documento = db.create_document("1", "a.pdf", "uploads/1/a.pdf", "application/pdf", 1)

    assert db.associate_document("1", "Cálculo", documento["id"]) is True
    assert db.associate_document("1", "Física", documento["id"]) is True
    # Cualquier documento autenticado puede asignarse a las materias del usuario.
    assert db.associate_document("2", "Cálculo", documento["id"]) is True

    de_calculo = db.list_documents("1", "Cálculo")
    assert [d["id"] for d in de_calculo] == [documento["id"]]

    db.remove_document_association("1", "Cálculo", documento["id"])
    assert db.list_documents("1", "Cálculo") == []
    assert len(db.list_documents("1")) == 1


def test_asociar_materia_a_documento_ajeno(db):
    assert db.associate_document("1", "Cálculo", "no-existe") is False


def test_asociacion_repetida_es_idempotente(db):
    documento = db.create_document("1", "a.pdf", "uploads/1/a.pdf", "application/pdf", 1)
    db.associate_document("1", "Cálculo", documento["id"])
    db.associate_document("1", "Cálculo", documento["id"])
    assert db.materia_documents_collection.count_documents({}) == 1


def test_eliminar_documento_y_sus_asociaciones(db):
    documento = db.create_document("1", "a.pdf", "uploads/1/a.pdf", "application/pdf", 1)
    db.associate_document("1", "Cálculo", documento["id"])

    eliminado = db.delete_document("1", documento["id"])
    assert eliminado is not None
    assert eliminado["id"] == documento["id"]
    assert db.get_document("1", documento["id"]) is None
    assert db.materia_documents_collection.count_documents({}) == 0


def test_eliminar_documento_inexistente_o_ajeno(db):
    documento = db.create_document("1", "a.pdf", "uploads/1/a.pdf", "application/pdf", 1)
    assert db.delete_document("2", documento["id"]) is None
    assert db.delete_document("1", "no-existe") is None


def test_storage_info_for_user(db):
    db.create_document("1", "a.pdf", "uploads/1/a.pdf", "application/pdf", 100)
    db.create_document("1", "b.pdf", "uploads/1/b.pdf", "application/pdf", 50)
    info = db.storage_info_for_user("1", 1000)
    assert info["used"] == 150
    assert info["available"] == 850
    assert info["documentCount"] == 2
    assert info["percent"] == 15.0


def test_revocar_sesiones_de_usuario(db):
    token_a = db.create_session("1")
    token_b = db.create_session("1")
    db.revoke_user_sessions("1", keep_token=token_a)
    assert db.user_id_for_token(token_a) == "1"
    assert db.user_id_for_token(token_b) is None


def test_delete_user_data(db):
    db.users_collection.insert_one({"_id": 1, "name": "Ana", "email": "ana@correo.com"})
    documento = db.create_document("1", "a.pdf", "uploads/1/a.pdf", "application/pdf", 1)
    db.associate_document("1", "Cálculo", documento["id"])
    db.tasks_collection.insert_one({"id": "t1", "user_id": "1", "title": "Tarea"})
    db.calendar_events_collection.insert_one({"id": "e1", "user_id": "1", "title": "Evento"})
    db.create_session("1")

    user, documentos, adjuntos = db.delete_user_data("1")
    assert user["name"] == "Ana"
    assert [d["id"] for d in documentos] == [documento["id"]]
    assert adjuntos == []
    assert db.users_collection.count_documents({}) == 0
    assert db.documents_collection.count_documents({}) == 0
    assert db.tasks_collection.count_documents({}) == 0
    assert db.calendar_events_collection.count_documents({}) == 0
    assert db.sessions_collection.count_documents({}) == 0
    assert db.materia_documents_collection.count_documents({}) == 0