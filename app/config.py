"""
Configuración central del backend.

Todo se lee de variables de entorno (vía `python-dotenv`, que carga el archivo
`.env` de la raíz del backend). Hay valores por defecto para desarrollo local.

CÓMO CAMBIAR LA CONEXIÓN A MONGO:
  - Edita `MONGODB_URL` / `MONGODB_DATABASE` en `backend/.env` (recomendado) o
    cambia los valores por defecto de aquí.
CÓMO CAMBIAR LOS ORÍGENES PERMITIDOS (CORS):
  - Edita la variable `CORS_ORIGINS` (lista separada por comas) en el entorno.
CÓMO CAMBIAR DÓNDE SE GUARDAN LOS ARCHIVOS:
  - Edita `UPLOADS_DIR` en el entorno (útil en serverless, donde el disco es de
    solo lectura y hay que usar `/tmp/uploads`).
"""

from dotenv import load_dotenv
from pathlib import Path
import os

# Carga `.env` desde el directorio de trabajo actual. Si no existe, no falla.
# EFECTO: define variables en `os.environ` antes de leerlas abajo.
load_dotenv()

# Raíz del backend: el padre de la carpeta `app/` (donde vive este archivo).
BACKEND_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# BASE DE DATOS
# ---------------------------------------------------------------------------
# `MONGODB_URL` es la cadena de conexión (localhost en local, Atlas en prod).
# `MONGODB_DATABASE` es el nombre de la base dentro del cluster.
# EFECTO: los consume `app/database.py` para crear el cliente y elegir la base.
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "studyfinder")

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Lista de orígenes (esquema+host+puerto) que pueden llamar a la API desde un
# navegador. Se parte por comas, se recortan espacios y se descartan vacíos.
# EFECTO: la aplica `CORSMiddleware` en `app/main.py`.
# CÓMO CAMBIARLO: variable `CORS_ORIGINS="https://mi-front.com,http://localhost:4200"`.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:4200,http://127.0.0.1:4200").split(",")
    if origin.strip()
]

# ---------------------------------------------------------------------------
# ALMACENAMIENTO DE ARCHIVOS
# ---------------------------------------------------------------------------
# Carpeta donde se escriben los documentos y avatares. En Vercel el bundle es de
# solo lectura, por eso se permite sobreescribir con `UPLOADS_DIR` (p. ej. /tmp).
# NOTE: esta carpeta NO se crea aquí; se crea al escribir el primer archivo.
# EFECTO: la usan `app/api/documents.py`, `app/api/users.py` y la base de datos.
# CÓMO CAMBIARLO: define `UPLOADS_DIR` en el entorno.
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR") or BACKEND_ROOT / "uploads")
