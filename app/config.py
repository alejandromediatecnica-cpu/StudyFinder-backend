from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv()

BACKEND_ROOT = Path(__file__).resolve().parent.parent

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "studyfinder")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:4200,http://127.0.0.1:4200").split(",")
    if origin.strip()
]
# En entornos serverless (Vercel) el código es de solo lectura: usar /tmp/uploads.
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR") or BACKEND_ROOT / "uploads")
