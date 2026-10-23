"""
Autenticación: registro, login, cambio de contraseña en dos pasos y logout.

MODELO DE AUTENTICACIÓN
  - Las contraseñas se guardan con `hashlib.scrypt` (n=2^14, r=8, p=1, dklen=64)
    y una sal aleatoria de 16 bytes. Las cuentas antiguas usaban PBKDF2-HMAC
    SHA256 (120k iteraciones) y se migran automáticamente a scrypt al iniciar
    sesión (ver `password_needs_rehash` + `_store_refreshed_hash`).
  - El login devuelve un token OPACO (no JWT) creado en
    `app/database.py:create_session`; el cliente lo envía como `Bearer`.
  - Los errores de login son genéricos para no revelar si el correo existe.

CÓMO CAMBIAR LA LONGITUD/FUERZA DE CONTRASEÑA
  - `validate_password_strength` (mínimo 12, mayúscula, minúscula, dígito,
    lista de comunes). Para cambiar el mínimo, edita esa función y también
    `SignupRequest.password` / `ChangePasswordRequest` (Field min_length=12).
CÓMO CAMBIAR EL ALGORITMO DE HASH
  - `hash_password` y `verify_password`. Mantén la rama legacy mientras existan
    cuentas antiguas.
CÓMO AÑADIR UN PASO EXTRA AL LOGIN (p. ej. 2FA)
  - Justo después de validar la contraseña, antes de `create_session`.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import DuplicateKeyError

from app.database import (
    create_session,
    normalize_name_key,
    password_verifications_collection,
    revoke_session,
    revoke_user_sessions,
    users_collection,
)
from app.deps import get_current_user
from app.rate_limit import check_rate_limit

# Todas las rutas cuelgan de `/auth` y aparecen agrupadas en `/docs` con tag "auth".
router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# PARÁMETROS CRIPTOGRÁFICOS
# ---------------------------------------------------------------------------
# n=2^14, r=8, p=1 son los valores recomendados por la RFC 7914 para scrypt.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
# Vida del token temporal del cambio de contraseña.
VERIFICATION_MINUTES = 10

# Lista mínima de contraseñas prohibidas (se compara en minúsculas).
# CÓMO AMPLIARLA: añade más cadenas aquí (todas en minúsculas).
COMMON_PASSWORDS = {
    "123456789012",
    "contrasena123",
    "contraseña123",
    "password1234",
    "qwerty123456",
    "estudiar1234",
    "studyfinder1",
}

# Mensajes genéricos: no distinguen "correo inexistente" de "contraseña mala"
# (evita enumeración de cuentas).
GENERIC_LOGIN_ERROR = "Correo electrónico o contraseña incorrectos."
GENERIC_SIGNUP_ERROR = (
    "No se pudo crear la cuenta con esos datos. Prueba con otro correo o nombre de usuario."
)


# ---------------------------------------------------------------------------
# ESQUEMAS DE ENTRADA (Pydantic)
# ---------------------------------------------------------------------------
class SignupRequest(BaseModel):
    # Nombre visible (1..100). `email` valida formato; `password` 12..128.
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    # En login NO se valida la fuerza: solo que no venga vacía (evita filtrar reglas).
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyPasswordRequest(BaseModel):
    # Primer paso del cambio: correo + contraseña actual.
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    # Segundo paso: token temporal + nueva contraseña repetida.
    verification_token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)


# ---------------------------------------------------------------------------
# HASHING
# ---------------------------------------------------------------------------
def _salt_bytes(salt: str) -> bytes:
    """Convierte la sal guardada a bytes.

    Las sales nuevas son hex; algunas antiguas podrían no serlo, en cuyo caso se
    codifican como UTF-8. Esto mantiene compatibilidad.
    """
    try:
        return bytes.fromhex(salt)
    except ValueError:
        return salt.encode()


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Hashea una contraseña con scrypt.

    Si no se pasa `salt`, genera una nueva (`secrets.token_hex(16)` = 16 bytes).
    `maxmem=64MB` debe ser suficiente para n=2^14, r=8 (OpenSSL lo exige).
    RETORNO: (hash hex, sal hex/str).
    EFECTO: ninguno en BD; solo cálculo.
    """
    salt_value = salt or secrets.token_hex(16)
    password_hash = hashlib.scrypt(
        password.encode(),
        salt=_salt_bytes(salt_value),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=64 * 1024 * 1024,
    ).hex()
    return password_hash, salt_value


def password_needs_rehash(user: dict) -> bool:
    """True si el usuario no usa scrypt (cuenta antigua con PBKDF2)."""
    return user.get("password_algo") != "scrypt"


def verify_password(user: dict, password: str) -> bool:
    """Comprueba una contraseña contra el hash guardado.

    - Si `password_algo == "scrypt"`, recalcula con scrypt y la sal del usuario.
    - Si no, usa PBKDF2-HMAC-SHA256 (120k iteraciones) para cuentas legacy.
    Compara en tiempo constante con `secrets.compare_digest` (evita timing attacks).
    RETORNO: True si coincide.
    """
    salt = user.get("password_salt", "")
    if user.get("password_algo") == "scrypt":
        candidate, _ = hash_password(password, salt)
    else:
        # Compatibilidad con las cuentas creadas antes de migrar a scrypt.
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), 120_000
        ).hex()
    return secrets.compare_digest(candidate, user.get("password_hash", ""))


def validate_password_strength(password: str) -> None:
    """Valida la política de contraseña; lanza 422 con un mensaje en español.

    Reglas: >=12 caracteres, no estar en `COMMON_PASSWORDS`, y contener al menos
    una minúscula, una mayúscula y un dígito.
    EFECTO: solo lanza excepciones; no devuelve nada.
    CÓMO CAMBIAR LA POLÍTICA: modifica las comprobaciones de esta función.
    """
    if len(password) < 12:
        raise HTTPException(
            status_code=422,
            detail="La contraseña debe tener al menos 12 caracteres.",
        )
    if password.casefold() in COMMON_PASSWORDS:
        raise HTTPException(
            status_code=422,
            detail="Esa contraseña es demasiado común. Elige una más difícil de adivinar.",
        )
    if not any(char.islower() for char in password):
        raise HTTPException(status_code=422, detail="La contraseña debe incluir una minúscula.")
    if not any(char.isupper() for char in password):
        raise HTTPException(status_code=422, detail="La contraseña debe incluir una mayúscula.")
    if not any(char.isdigit() for char in password):
        raise HTTPException(status_code=422, detail="La contraseña debe incluir un número.")


def user_response(user: dict, token: str) -> dict:
    """Construye la respuesta de autenticación `{token, user}`.

    Expone solo id, name y email (nunca el hash ni la sal).
    CÓMO AÑADIR UN CAMPO AL USUARIO DEVUELTO: añádelo aquí (y en el frontend,
    `AuthUser` en `auth.service.ts`).
    """
    return {
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "name": user["name"],
            "email": user["email"],
        },
    }


def _store_refreshed_hash(user: dict, password: str) -> None:
    """Rehashea la contraseña con scrypt y actualiza el usuario (migración).

    Se ejecuta tras un login correcto de una cuenta legacy. EFECTO: escribe en
    `users` (`password_hash`, `password_salt`, `password_algo`).
    """
    password_hash, salt = hash_password(password)
    users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": password_hash, "password_salt": salt, "password_algo": "scrypt"}},
    )


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------
@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, request: Request):
    """Registra una cuenta y devuelve una sesión ya iniciada.

    Flujo:
      1. Rate limit por IP (40 registros/hora).
      2. Recorta el nombre y valida la contraseña.
      3. Normaliza email (minúsculas) y `name_key`; rechaza duplicados (409).
      4. Hashea y guarda el usuario.
      5. Crea sesión y devuelve {token, user}.

    EFECTOS: escrituras en `users` y `auth_sessions`; puede registrar en el log
    del rate limit. NO emite evento SSE (nadie más debe saber del registro).
    CÓMO CAMBIAR CAMPOS INICIALES: edita el dict `user` (p. ej. `language`).
    CÓMO DESACTIVAR EL AUTO-LOGIN TRAS REGISTRO: devuelve solo el usuario, sin
    `create_session`, y ajusta el frontend (`SignupComponent` navega a /main).
    """
    # 1) Freno anti-abuso por IP.
    if not check_rate_limit(f"signup:{request.client.host if request.client else 'local'}", 40, 3600):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
        )

    # 2) Nombre limpio + política de contraseña.
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío.")
    validate_password_strength(payload.password)

    # 3) Unicidad por email (minúsculas) y por nombre normalizado.
    email = payload.email.lower()
    name_key = normalize_name_key(name)

    if users_collection.find_one({"email": email}) or users_collection.find_one({"name_key": name_key}):
        raise HTTPException(status_code=409, detail=GENERIC_SIGNUP_ERROR)

    # 4) Hash + documento de usuario.
    password_hash, salt = hash_password(payload.password)
    user = {
        "name": name,
        "name_key": name_key,
        "email": email,
        "password_hash": password_hash,
        "password_salt": salt,
        "password_algo": "scrypt",
        "language": "es",
        "avatar_path": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # El índice único de email/name_key también protege ante carreras: si dos
    # peticiones pasan el `find_one`, la segunda choca aquí con DuplicateKeyError.
    try:
        result = users_collection.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=GENERIC_SIGNUP_ERROR)
    user["_id"] = result.inserted_id

    # 5) Auto-login.
    return user_response(user, create_session(str(user["_id"])))


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    """Inicia sesión y devuelve {token, user}.

    Flujo:
      1. Rate limit por (email, IP): 20 intentos / 5 min.
      2. Busca usuario por email en minúsculas.
      3. Verifica contraseña (scrypt o PBKDF2 legacy). Si algo falla -> 401 genérico.
      4. Migra el hash si era legacy.
      5. Crea sesión.

    EFECTOS: posible escritura en `users` (rehash) e inserción en `auth_sessions`.
    CÓMO AÑADIR 2FA: entre los pasos 3 y 5.
    CÓMO CAMBIAR EL LÍMITE: los dos números de `check_rate_limit` (20, 300).
    """
    # 1) Freno de fuerza bruta.
    if not check_rate_limit(
        f"login:{payload.email.lower()}:{request.client.host if request.client else 'local'}",
        20,
        300,
    ):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
        )

    # 2) Usuario por email normalizado.
    email = payload.email.lower()
    user = users_collection.find_one({"email": email})

    # 3) Credenciales. Mismo error para usuario inexistente y contraseña mala.
    if not user or not user.get("password_hash") or not verify_password(user, payload.password):
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)

    # 4) Migración transparente PBKDF2 -> scrypt.
    if password_needs_rehash(user):
        _store_refreshed_hash(user, payload.password)

    # 5) Nueva sesión.
    return user_response(user, create_session(str(user["_id"])))


@router.post("/password/verify")
def verify_current_password(
    payload: VerifyPasswordRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Paso 1 del cambio de contraseña: confirma la contraseña actual.

    Requiere sesión (`Depends(get_current_user)`), así que solo el dueño puede
    iniciar el cambio. Valida que el email enviado sea el de la cuenta y que la
    contraseña coincida; luego crea un token temporal de 10 minutos.

    EFECTOS: rate limit (10 / 5 min) e inserción en `password_verifications`.
    RETORNO: {verification_token, expires_in: 600}.
    CÓMO CAMBIAR LA VIDA DEL TOKEN: `VERIFICATION_MINUTES`.
    """
    if not check_rate_limit(
        f"password-verify:{current_user['_id']}:{request.client.host if request.client else 'local'}",
        10,
        300,
    ):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
        )

    # El email debe ser el de la cuenta autenticada y la contraseña debe coincidir.
    email = payload.email.lower()
    if email != current_user.get("email") or not verify_password(current_user, payload.password):
        raise HTTPException(status_code=400, detail=GENERIC_LOGIN_ERROR)

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_MINUTES)
    password_verifications_collection.insert_one(
        {
            "token": token,
            "user_id": str(current_user["_id"]),
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc),
        }
    )
    return {"verification_token": token, "expires_in": VERIFICATION_MINUTES * 60}


@router.post("/password/change")
def change_password(
    payload: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    """Paso 2: cambia la contraseña usando el token temporal.

    Flujo:
      1. `new_password` debe coincidir con `confirm_password`.
      2. El token debe existir y pertenecer al usuario; si expiró -> 400 y se borra.
      3. La nueva contraseña cumple la política y es distinta de la actual.
      4. Guarda el nuevo hash, borra los tokens de verificación del usuario y
         revoca TODAS las sesiones.

    EFECTOS: escritura en `users`; borrados en `password_verifications` y
    `auth_sessions`; crea una sesión nueva (la que devuelve).
    IMPORTANTE: al revocar sesiones, el resto de dispositivos tendrá que volver a
    iniciar sesión. El frontend (`changePassword`) guarda el token nuevo.
    """
    # 1) Confirmación.
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Las contraseñas nuevas no coinciden.")

    # 2) Token de verificación válido y del usuario.
    verification = password_verifications_collection.find_one(
        {"token": payload.verification_token, "user_id": str(current_user["_id"])}
    )
    if not verification:
        raise HTTPException(
            status_code=400,
            detail="La verificación expiró. Vuelve a confirmar tu contraseña actual.",
        )
    # Normaliza la zona horaria por si Mongo devolvió un datetime naive.
    expires_at = verification.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            password_verifications_collection.delete_one({"_id": verification["_id"]})
            raise HTTPException(
                status_code=400,
                detail="La verificación expiró. Vuelve a confirmar tu contraseña actual.",
            )

    # 3) Política + la nueva no puede ser la actual.
    validate_password_strength(payload.new_password)
    if verify_password(current_user, payload.new_password):
        raise HTTPException(
            status_code=400,
            detail="La contraseña nueva debe ser distinta de la actual.",
        )

    # 4) Persistencia y revocación total de sesiones.
    password_hash, salt = hash_password(payload.new_password)
    users_collection.update_one(
        {"_id": current_user["_id"]},
        {
            "$set": {
                "password_hash": password_hash,
                "password_salt": salt,
                "password_algo": "scrypt",
            }
        },
    )
    password_verifications_collection.delete_many({"user_id": str(current_user["_id"])})
    # Se invalidan todas las sesiones anteriores y se entrega una nueva.
    revoke_user_sessions(str(current_user["_id"]))
    return user_response(current_user, create_session(str(current_user["_id"])))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: str | None = Header(default=None)):
    """Cierra la sesión del token enviado (borra la fila en `auth_sessions`).

    No usa `Depends(get_current_user)` para poder responder 401 con un mensaje
    concreto si falta la cabecera. Un token ya inválido también devuelve 204
    (borrar algo inexistente es idempotente).
    EFECTOS: borra una sesión.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    revoke_session(authorization.removeprefix("Bearer ").strip())
