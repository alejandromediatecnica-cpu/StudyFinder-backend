import hashlib
import secrets
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import DuplicateKeyError

from app.database import create_session, revoke_session, users_collection

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt_value = salt or secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt_value.encode(), 120_000
    ).hex()
    return password_hash, salt_value


def user_response(user: dict, token: str) -> dict:
    return {
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "name": user["name"],
            "email": user["email"],
        },
    }


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest):
    email = payload.email.lower()
    if users_collection.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="El correo ya esta registrado")

    password_hash, salt = hash_password(payload.password)
    user = {
        "name": payload.name.strip(),
        "email": email,
        "password_hash": password_hash,
        "password_salt": salt,
    }
    try:
        result = users_collection.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="El correo ya esta registrado")
    user["_id"] = result.inserted_id
    return user_response(user, create_session(str(user["_id"])))


@router.post("/login")
def login(payload: LoginRequest):
    email = payload.email.lower()
    user = users_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=401, detail="Correo o contrasena incorrectos")

    password_hash, _ = hash_password(payload.password, user["password_salt"])
    if not secrets.compare_digest(password_hash, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Correo o contrasena incorrectos")

    return user_response(user, create_session(str(user["_id"])))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    revoke_session(authorization.removeprefix("Bearer ").strip())
