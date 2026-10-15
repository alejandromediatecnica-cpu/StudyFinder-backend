from app.api.auth import hash_password, user_response
from app.database import user_id_for_token


class TestHashPassword:
    def test_sal_nueva_por_contrasena(self):
        hash_a, sal_a = hash_password("clave123")
        hash_b, sal_b = hash_password("clave123")
        assert sal_a != sal_b
        assert hash_a != hash_b

    def test_determinista_con_la_misma_sal(self):
        hash_a, sal = hash_password("clave", salt="mi-sal")
        hash_b, _ = hash_password("clave", salt="mi-sal")
        assert hash_a == hash_b

    def test_no_guarda_la_contrasena_plana(self):
        hash_, _ = hash_password("clave")
        assert hash_ != "clave"


class TestUserResponse:
    def test_formato_de_respuesta(self):
        respuesta = user_response({"_id": 7, "name": "Ana", "email": "a@b.co"}, "tok-1")
        assert respuesta == {
            "token": "tok-1",
            "user": {"id": "7", "name": "Ana", "email": "a@b.co"},
        }


class TestSignup:
    def test_registra_y_crea_sesion(self, client):
        respuesta = client.post(
            "/auth/signup",
            json={"name": "Ana", "email": "ana@correo.com", "password": "secreta1"},
        )
        assert respuesta.status_code == 201
        datos = respuesta.json()
        assert datos["token"]
        assert datos["user"]["id"]
        assert datos["user"]["name"] == "Ana"
        assert datos["user"]["email"] == "ana@correo.com"
        assert user_id_for_token(datos["token"]) == datos["user"]["id"]

    def test_signup_normaliza_el_correo(self, client):
        respuesta = client.post(
            "/auth/signup",
            json={"name": "Ana", "email": "ANA@Correo.COM", "password": "secreta1"},
        )
        assert respuesta.status_code == 201
        assert respuesta.json()["user"]["email"] == "ana@correo.com"

    def test_signup_duplicado_rechaza(self, client):
        cuerpo = {"name": "Ana", "email": "ana@correo.com", "password": "secreta1"}
        client.post("/auth/signup", json=cuerpo)
        respuesta = client.post("/auth/signup", json=cuerpo)
        assert respuesta.status_code == 409
        assert respuesta.json()["detail"] == "El correo ya esta registrado"

    def test_signup_correo_mismo_con_mayusculas(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": "secreta1"})
        respuesta = client.post(
            "/auth/signup", json={"name": "Ana", "email": "ANA@CORREO.COM", "password": "secreta1"}
        )
        assert respuesta.status_code == 409

    def test_signup_correo_invalido(self, client):
        respuesta = client.post(
            "/auth/signup", json={"name": "Ana", "email": "no-es-correo", "password": "secreta1"}
        )
        assert respuesta.status_code == 422

    def test_signup_contrasena_corta(self, client):
        respuesta = client.post(
            "/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": "123"}
        )
        assert respuesta.status_code == 422

    def test_signup_nombre_vacio(self, client):
        respuesta = client.post(
            "/auth/signup", json={"name": "", "email": "ana@correo.com", "password": "secreta1"}
        )
        assert respuesta.status_code == 422


class TestLogin:
    def test_login_crea_token(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": "secreta1"})
        respuesta = client.post(
            "/auth/login", json={"email": "ana@correo.com", "password": "secreta1"}
        )
        assert respuesta.status_code == 200
        datos = respuesta.json()
        assert datos["token"]
        assert datos["user"]["email"] == "ana@correo.com"

    def test_login_ignora_mayusculas_en_correo(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": "secreta1"})
        respuesta = client.post(
            "/auth/login", json={"email": "ANA@CORREO.COM", "password": "secreta1"}
        )
        assert respuesta.status_code == 200

    def test_login_contrasena_incorrecta(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": "secreta1"})
        respuesta = client.post(
            "/auth/login", json={"email": "ana@correo.com", "password": "incorrecta"}
        )
        assert respuesta.status_code == 401
        assert respuesta.json()["detail"] == "Correo o contrasena incorrectos"

    def test_login_correo_desconocido(self, client):
        respuesta = client.post(
            "/auth/login", json={"email": "nadie@correo.com", "password": "secreta1"}
        )
        assert respuesta.status_code == 401


class TestLogout:
    def test_logout_sin_token_rechaza(self, client):
        respuesta = client.post("/auth/logout")
        assert respuesta.status_code == 401

    def test_logout_con_header_invalido(self, client):
        respuesta = client.post("/auth/logout", headers={"Authorization": "Token raro"})
        assert respuesta.status_code == 401

    def test_logout_revoca_la_sesion(self, client, user):
        respuesta = client.post("/auth/logout", headers=user["headers"])
        assert respuesta.status_code == 204
        assert user_id_for_token(user["token"]) is None

    def test_el_token_revocado_deja_de_servir(self, client, user):
        client.post("/auth/logout", headers=user["headers"])
        respuesta = client.get("/documents", headers=user["headers"])
        assert respuesta.status_code == 401