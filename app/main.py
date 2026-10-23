import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import ConnectionFailure
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

logger = logging.getLogger(__name__)

app = FastAPI(title="StudyFinder API", version="1.0.0")


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.exception_handler(ConnectionFailure)
def database_unavailable(request: Request, error: ConnectionFailure) -> JSONResponse:
    # Sin este handler la caída de MongoDB sería un 500 sin cabeceras CORS, que el
    # navegador muestra como error de CORS en vez del problema real.
    logger.error("MongoDB no disponible en %s %s: %s", request.method, request.url.path, error)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "La base de datos no está disponible. Inténtalo de nuevo en unos minutos."},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(users_router)
app.include_router(contacts_router)
app.include_router(messages_router)
app.include_router(messages_files_router)
app.include_router(tasks_router)
app.include_router(calendar_router)
app.include_router(events_router)


@app.get("/")
def read_root():
    return {"message": "Backend de StudyFinder operando correctamente"}
