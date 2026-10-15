import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router
from app.api.documents import router as documents_router
from app.api.search import router as search_router
from app.api.users import router as users_router
from app.api.contacts import router as contacts_router
from app.api.messages import router as messages_router
from app.api.messages_files import router as messages_files_router
from app.config import CORS_ORIGINS
from app.database import initialize_database

app = FastAPI(title="StudyFinder API", version="1.0.0")


@app.on_event("startup")
def startup() -> None:
    initialize_database()


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


@app.get("/")
def read_root():
    return {"message": "Backend de StudyFinder operando correctamente"}
