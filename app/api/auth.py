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

router = APIRouter(prefix="/auth", tags=["auth"])

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
VERIFICATION_MINUTES = 10

COMMON_PASSWORDS = {
    "123456789012",
    "contrasena123",
    "contraseña123",
    "password1234",
    "qwerty123456",
    "estudiar1234",
    "studyfinder1",
}

GENERIC_LOGIN_ERROR = "Correo electrónico o contraseña incorrectos."
GENERIC_SIGNUP_ERROR = (
    "No se pudo crear la cuenta con esos datos. Prueba con otro correo o nombre de usuario."
)


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyPasswordRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    verification_token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)


def _salt_bytes(salt: str) -> bytes:
    try:
        return bytes.fromhex(salt)
    except ValueError:
        return salt.encode()


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
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
    return user.get("password_algo") != "scrypt"


def verify_password(user: dict, password: str) -> bool:
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
    return {
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "name": user["name"],
            "email": user["email"],
        },
    }


def _store_refreshed_hash(user: dict, password: str) -> None:
    password_hash, salt = hash_password(password)
    users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": password_hash, "password_salt": salt, "password_algo": "scrypt"}},
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, request: Request):
    if not check_rate_limit(f"signup:{request.client.host if request.client else 'local'}", 40, 3600):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
        )

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío.")
    validate_password_strength(payload.password)
    email = payload.email.lower()
    name_key = normalize_name_key(name)

    if users_collection.find_one({"email": email}) or users_collection.find_one({"name_key": name_key}):
        raise HTTPException(status_code=409, detail=GENERIC_SIGNUP_ERROR)

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
    try:
        result = users_collection.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=GENERIC_SIGNUP_ERROR)
    user["_id"] = result.inserted_id
    return user_response(user, create_session(str(user["_id"])))


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    if not check_rate_limit(
        f"login:{payload.email.lower()}:{request.client.host if request.client else 'local'}",
        20,
        300,
    ):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
        )

    email = payload.email.lower()
    user = users_collection.find_one({"email": email})
    if not user or not user.get("password_hash") or not verify_password(user, payload.password):
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)

    if password_needs_rehash(user):
        _store_refreshed_hash(user, payload.password)

    return user_response(user, create_session(str(user["_id"])))


@router.post("/password/verify")
def verify_current_password(
    payload: VerifyPasswordRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if not check_rate_limit(
        f"password-verify:{current_user['_id']}:{request.client.host if request.client else 'local'}",
        10,
        300,
    ):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
        )

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
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Las contraseñas nuevas no coinciden.")

    verification = password_verifications_collection.find_one(
        {"token": payload.verification_token, "user_id": str(current_user["_id"])}
    )
    if not verification:
        raise HTTPException(
            status_code=400,
            detail="La verificación expiró. Vuelve a confirmar tu contraseña actual.",
        )
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

    validate_password_strength(payload.new_password)
    if verify_password(current_user, payload.new_password):
        raise HTTPException(
            status_code=400,
            detail="La contraseña nueva debe ser distinta de la actual.",
        )

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
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    revoke_session(authorization.removeprefix("Bearer ").strip())
