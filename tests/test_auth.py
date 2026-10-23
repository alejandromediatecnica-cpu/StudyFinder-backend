import app.database as database
from app.api.auth import hash_password, user_response, verify_password
from app.database import user_id_for_token

PASSWORD = "ClaveSegura2026x"


class TestHashPassword:
    def test_sal_nueva_por_contrasena(self):
        hash_a, sal_a = hash_password("clave12345678")
        hash_b, sal_b = hash_password("clave12345678")
        assert sal_a != sal_b
        assert hash_a != hash_b

    def test_determinista_con_la_misma_sal(self):
        hash_a, sal = hash_password("clave", salt="0123456789abcdef")
        hash_b, _ = hash_password("clave", salt="0123456789abcdef")
        assert hash_a == hash_b

    def test_no_guarda_la_contrasena_plana(self):
        hash_, _ = hash_password("clave")
        assert hash_ != "clave"

    def test_verifica_contrasena_scrypt(self):
        hash_, sal = hash_password(PASSWORD)
        user = {"password_hash": hash_, "password_salt": sal, "password_algo": "scrypt"}
        assert verify_password(user, PASSWORD) is True
        assert verify_password(user, "otra-clave") is False

    def test_verifica_compatibilidad_pbkdf2(self):
        import hashlib

        sal = "sal-antigua"
        legacy = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), sal.encode(), 120_000).hex()
        user = {"password_hash": legacy, "password_salt": sal}
        assert verify_password(user, PASSWORD) is True


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
            json={"name": "Ana", "email": "ana@correo.com", "password": PASSWORD},
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
            json={"name": "Ana", "email": "ANA@Correo.COM", "password": PASSWORD},
        )
        assert respuesta.status_code == 201
        assert respuesta.json()["user"]["email"] == "ana@correo.com"

    def test_signup_duplicado_rechaza(self, client):
        cuerpo = {"name": "Ana", "email": "ana@correo.com", "password": PASSWORD}
        client.post("/auth/signup", json=cuerpo)
        respuesta = client.post("/auth/signup", json={"name": "Otra", **cuerpo})
        assert respuesta.status_code == 409
        assert "otro correo" in respuesta.json()["detail"]

    def test_signup_correo_mismo_con_mayusculas(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": PASSWORD})
        respuesta = client.post(
            "/auth/signup", json={"name": "Otra", "email": "ANA@CORREO.COM", "password": PASSWORD}
        )
        assert respuesta.status_code == 409

    def test_signup_nombre_duplicado_rechaza(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": PASSWORD})
        respuesta = client.post(
            "/auth/signup", json={"name": "  ana ", "email": "otra@correo.com", "password": PASSWORD}
        )
        assert respuesta.status_code == 409

    def test_signup_nombre_duplicado_ignora_acentos(self, client):
        client.post("/auth/signup", json={"name": "José Pérez", "email": "jose@correo.com", "password": PASSWORD})
        respuesta = client.post(
            "/auth/signup", json={"name": "jose perez", "email": "otro@correo.com", "password": PASSWORD}
        )
        assert respuesta.status_code == 409

    def test_signup_correo_invalido(self, client):
        respuesta = client.post(
            "/auth/signup", json={"name": "Ana", "email": "no-es-correo", "password": PASSWORD}
        )
        assert respuesta.status_code == 422

    def test_signup_contrasena_corta(self, client):
        respuesta = client.post(
            "/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": "Corta123"}
        )
        assert respuesta.status_code == 422

    def test_signup_contrasena_sin_mayuscula(self, client):
        respuesta = client.post(
            "/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": "clavesegura2026"}
        )
        assert respuesta.status_code == 422

    def test_signup_nombre_vacio(self, client):
        respuesta = client.post(
            "/auth/signup", json={"name": "", "email": "ana@correo.com", "password": PASSWORD}
        )
        assert respuesta.status_code == 422


class TestLogin:
    def test_login_crea_token(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": PASSWORD})
        respuesta = client.post(
            "/auth/login", json={"email": "ana@correo.com", "password": PASSWORD}
        )
        assert respuesta.status_code == 200
        datos = respuesta.json()
        assert datos["token"]
        assert datos["user"]["email"] == "ana@correo.com"

    def test_login_ignora_mayusculas_en_correo(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": PASSWORD})
        respuesta = client.post(
            "/auth/login", json={"email": "ANA@CORREO.COM", "password": PASSWORD}
        )
        assert respuesta.status_code == 200

    def test_login_contrasena_incorrecta(self, client):
        client.post("/auth/signup", json={"name": "Ana", "email": "ana@correo.com", "password": PASSWORD})
        respuesta = client.post(
            "/auth/login", json={"email": "ana@correo.com", "password": "Incorrecta2026x"}
        )
        assert respuesta.status_code == 401
        assert respuesta.json()["detail"] == "Correo electrónico o contraseña incorrectos."

    def test_login_correo_desconocido(self, client):
        respuesta = client.post(
            "/auth/login", json={"email": "nadie@correo.com", "password": PASSWORD}
        )
        assert respuesta.status_code == 401
        # No filtra si el correo existe: mismo mensaje que con contraseña incorrecta.
        assert respuesta.json()["detail"] == "Correo electrónico o contraseña incorrectos."

    def test_login_migra_el_hash_antiguo(self, client, user):
        import hashlib

        sal = "sal-antigua"
        legacy = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), sal.encode(), 120_000).hex()
        database.users_collection.update_one(
            {"email": "ana@correo.com"},
            {"$set": {"password_hash": legacy, "password_salt": sal, "password_algo": None}},
        )
        assert client.post(
            "/auth/login", json={"email": "ana@correo.com", "password": PASSWORD}
        ).status_code == 200
        actualizado = database.users_collection.find_one({"email": "ana@correo.com"})
        assert actualizado["password_algo"] == "scrypt"


class TestCambioDeContrasena:
    def test_flujo_completo(self, client, user):
        verificacion = client.post(
            "/auth/password/verify",
            json={"email": user["email"], "password": user["password"]},
            headers=user["headers"],
        )
        assert verificacion.status_code == 200
        token = verificacion.json()["verification_token"]

        cambio = client.post(
            "/auth/password/change",
            json={
                "verification_token": token,
                "new_password": "NuevaClave2026x",
                "confirm_password": "NuevaClave2026x",
            },
            headers=user["headers"],
        )
        assert cambio.status_code == 200
        nuevo_token = cambio.json()["token"]
        assert nuevo_token

        # La contraseña anterior deja de servir.
        assert client.post(
            "/auth/login", json={"email": user["email"], "password": user["password"]}
        ).status_code == 401
        assert client.post(
            "/auth/login", json={"email": user["email"], "password": "NuevaClave2026x"}
        ).status_code == 200
        # El token anterior fue revocado; el nuevo sigue vigente.
        assert client.get("/users/me", headers=user["headers"]).status_code == 401
        assert client.get(
            "/users/me", headers={"Authorization": f"Bearer {nuevo_token}"}
        ).status_code == 200

    def test_verificacion_con_contrasena_incorrecta(self, client, user):
        respuesta = client.post(
            "/auth/password/verify",
            json={"email": user["email"], "password": "Incorrecta2026x"},
            headers=user["headers"],
        )
        assert respuesta.status_code == 400

    def test_token_de_verificacion_invalido(self, client, user):
        respuesta = client.post(
            "/auth/password/change",
            json={
                "verification_token": "token-invalido-123",
                "new_password": "NuevaClave2026x",
                "confirm_password": "NuevaClave2026x",
            },
            headers=user["headers"],
        )
        assert respuesta.status_code == 400

    def test_confirmacion_distinta(self, client, user):
        verificacion = client.post(
            "/auth/password/verify",
            json={"email": user["email"], "password": user["password"]},
            headers=user["headers"],
        )
        respuesta = client.post(
            "/auth/password/change",
            json={
                "verification_token": verificacion.json()["verification_token"],
                "new_password": "NuevaClave2026x",
                "confirm_password": "NuevaClave2026y",
            },
            headers=user["headers"],
        )
        assert respuesta.status_code == 400


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


class TestEliminarCuenta:
    def test_elimina_cuenta_y_sesiones(self, client, user):
        respuesta = client.request(
            "DELETE",
            "/users/me",
            json={"password": user["password"]},
            headers=user["headers"],
        )
        assert respuesta.status_code == 204
        assert client.get("/users/me", headers=user["headers"]).status_code == 401
        assert client.post(
            "/auth/login", json={"email": user["email"], "password": user["password"]}
        ).status_code == 401

    def test_contrasena_incorrecta_no_elimina(self, client, user):
        respuesta = client.request(
            "DELETE",
            "/users/me",
            json={"password": "Incorrecta2026x"},
            headers=user["headers"],
        )
        assert respuesta.status_code == 400
        assert client.get("/users/me", headers=user["headers"]).status_code == 200
