from dotenv import load_dotenv
import os

load_dotenv()

API_URL = os.getenv("API_URL")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "studyfinder")