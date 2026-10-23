"""
Punto de entrada de la API de StudyFinder.

Este archivo hace cuatro cosas, en este orden:
  1. Permite que el paquete `app.` se importe aunque se arranque desde otra carpeta.
  2. Crea la instancia de FastAPI y registra los manejadores globales (errores, CORS).
  3. Incluye (monta) todos los routers de la carpeta `app/api/`.
  4. Declara un endpoint de salud `GET /`.

Cómo cambiarlo:
  - Para añadir un endpoint nuevo: crea el router en `app/api/<nombre>.py` y agrégalo
    abajo con `app.include_router(...)`.
  - Para cambiar los orígenes permitidos: edita `CORS_ORIGINS` (variable de entorno)
    o `app/config.py`. La lista se aplica en la llamada a `CORSMiddleware`.
"""

import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# AJUSTE DE RUTA DE IMPORTACIÓN
# ---------------------------------------------------------------------------
# `uvicorn app.main:app` puede ejecutarse desde la raíz del backend o desde otra
# carpeta. Insertamos la raíz del proyecto (el padre de `app/`) en `sys.path`
# para que `import app.xxx` funcione siempre.
# EFECTO: modifica el estado global de `sys.path` en tiempo de importación.
# CÓMO CAMBIARLO: no suele hacer falta; si empaquetas el proyecto (layout src/)
# esta inserción deja de ser necesaria y puede eliminarse.
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import ConnectionFailure

# ---------------------------------------------------------------------------
# IMPORTACIÓN DE ROUTERS
# ---------------------------------------------------------------------------
# Cada router agrupa los endpoints de un dominio. El alias `*_router` evita
# choques de nombres al importarlos todos juntos.
# CÓMO AÑADIR UN DOMINIO: crea `app/api/mi_dominio.py` con
#   router = APIRouter(prefix="/mi-dominio", tags=["mi-dominio"])
# y luego impórtalo aquí y móntalo abajo con `app.include_router`.
from app.api.auth import router as auth_router
from app.api.calendar import router as calendar_router
from app.api.documents import router as documents_router
from app.api.events_stream import router as events_router
from app.api.search import router as search_router
from app.api.tasks import router as tasks_router
from app.api.users import router as users_router
from app.api.contacts import router as contacts_router
from app.api.messages import router as messages_router
from app.api.messages_files import router as messages_files_router
from app.config import CORS_ORIGINS
from app.database import initialize_database

# Logger del módulo: se usa en `database_unavailable` para registrar caídas de Mongo.
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# INSTANCIA DE LA APLICACIÓN
# ---------------------------------------------------------------------------
# Título y versión se muestran en la documentación automática (`/docs`).
# CÓMO CAMBIARLOS: edita los argumentos de `FastAPI(...)` aquí.
app = FastAPI(title="StudyFinder API", version="1.0.0")


# ---------------------------------------------------------------------------
# ARRANQUE
# ---------------------------------------------------------------------------
# `@app.on_event("startup")` se ejecuta una vez al arrancar el servidor.
# Llama a `initialize_database()`, que crea los índices de Mongo.
# NOTA: `app/database.py` ya llama a `initialize_database()` al importarse, así
# que esto es una segunda pasada idempotente (y útil si Mongo tardó en estar
# disponible en el import).
# CÓMO CAMBIARLO: FastAPI moderno prefiere `lifespan`; se puede migrar sin
# cambiar `initialize_database()`.
@app.on_event("startup")
def startup() -> None:
    initialize_database()


# ---------------------------------------------------------------------------
# MANEJADOR GLOBAL: MongoDB caído -> 503 con CORS
# ---------------------------------------------------------------------------
# Si Mongo lanza `ConnectionFailure`, sin este handler FastAPI devolvería un 500
# SIN cabeceras CORS; el navegador lo mostraría como error de CORS en vez de
# como "base de datos no disponible". Aquí lo convertimos en 503 con un mensaje
# claro en español. `CORSMiddleware` añade las cabeceras aunque sea un error.
# EFECTOS: escribe un log de error y devuelve un `JSONResponse`.
# CÓMO CAMBIARLO: para otro código HTTP o mensaje, edita `status_code`/`content`.
@app.exception_handler(ConnectionFailure)
def database_unavailable(request: Request, error: ConnectionFailure) -> JSONResponse:
    # Sin este handler la caída de MongoDB sería un 500 sin cabeceras CORS, que el
    # navegador muestra como error de CORS en vez del problema real.
    logger.error("MongoDB no disponible en %s %s: %s", request.method, request.url.path, error)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "La base de datos no está disponible. Inténtalo de nuevo en unos minutos."},
    )


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Permite que el frontend (otro origen/puerto) llame a la API. `CORS_ORIGINS`
# viene de `app/config.py` (variable de entorno `CORS_ORIGINS`).
# IMPORTANTE: `allow_credentials=True` + `allow_origins` explícito es lo correcto;
# no usar `["*"]` junto con credenciales.
# CÓMO CAMBIARLO: edita `CORS_ORIGINS` en el entorno o el valor por defecto.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# MONTAJE DE ROUTERS
# ---------------------------------------------------------------------------
# El ORDEN importa poco aquí porque los prefijos son distintos. Cada router ya
# trae su `prefix` (p. ej. `/auth`, `/tasks`).
# CÓMO AÑADIR/QUITAR UNA FUNCIONALIDAD: añade o comenta su `include_router`.
app.include_router(search_router)              # GET /search (OpenAlex, público)
app.include_router(auth_router)                # /auth/* (signup, login, password, logout)
app.include_router(documents_router)           # /documents/* (biblioteca, cuota, materias)
app.include_router(users_router)               # /users/* (perfil, avatar, búsqueda, baja)
app.include_router(contacts_router)            # /contacts/* (solicitudes y contactos)
app.include_router(messages_router)            # /messages/* (chat)
app.include_router(messages_files_router)      # /messages/files/* (descarga de adjuntos)
app.include_router(tasks_router)               # /tasks/* (tareas y borrado masivo)
app.include_router(calendar_router)            # /calendar/* (eventos)
app.include_router(events_router)              # /events/stream (SSE de tiempo real)


# ---------------------------------------------------------------------------
# SALUD
# ---------------------------------------------------------------------------
# Endpoint trivial para comprobar que el proceso está vivo (no toca Mongo).
# CÓMO CAMBIARLO: aquí mismo; no lo usan los tests como contrato salvo el de
# resiliencia, que espera `200` aunque Mongo esté caído.
@app.get("/")
def read_root():
    return {"message": "Backend de StudyFinder operando correctamente"}
