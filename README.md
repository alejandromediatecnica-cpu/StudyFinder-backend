# StudyFinder — Backend

API REST de **StudyFinder** hecha con **FastAPI** (Python 3.13). Guarda usuarios, sesiones,
documentos, contactos y mensajes en **MongoDB** (driver `pymongo`) y busca artículos
académicos en **OpenAlex**. Escucha en `http://localhost:8000`.

El frontend (Angular) vive en su propio repositorio, `StudyFinder-Frontend`, y necesita
este backend encendido para funcionar.

---

## Requisitos previos

| Herramienta | Versión | Para qué sirve | Comprobar |
| --- | --- | --- | --- |
| **uv** | 0.13 o superior | Gestiona Python, el entorno virtual y las dependencias | `uv --version` |
| **MongoDB** | Atlas o local | Base de datos | — |
| **Git** | Cualquiera reciente | Descargar el proyecto | `git --version` |

**No hace falta instalar Python a mano**: `uv` descarga Python 3.13 automáticamente
(la versión está fijada en `.python-version`).

### Instalar uv

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Cierra y vuelve a abrir la terminal para que `uv` quede en el `PATH`.

### Base de datos

Lo más fácil es **MongoDB Atlas** (gratis):

1. Crea una cuenta en <https://www.mongodb.com/atlas> y un clúster gratuito (*M0*).
2. En *Database Access* crea un usuario con contraseña.
3. En *Network Access* permite tu IP.
4. En *Connect → Drivers* copia la cadena de conexión (`mongodb+srv://...`).

Si tienes MongoDB instalado localmente, usa `mongodb://localhost:27017`.

---

## Puesta en marcha

Todos los comandos se ejecutan **en la raíz de este repositorio**.

### 1. Instalar dependencias

```bash
uv sync
```

Crea `.venv/` con Python 3.13 e instala exactamente las versiones de `uv.lock`
(incluidas las de desarrollo, como `pytest`).

### 2. Configurar variables de entorno

Copia la plantilla y rellénala con tus datos:

```powershell
Copy-Item .env.example .env      # Windows (PowerShell)
```

```bash
cp .env.example .env             # macOS / Linux
```

| Variable | Descripción | Valor por defecto |
| --- | --- | --- |
| `MONGODB_URL` | Cadena de conexión a MongoDB (Atlas `mongodb+srv://...` o local). **Secreto**: incluye usuario y contraseña. | `mongodb://localhost:27017` |
| `MONGODB_DATABASE` | Nombre de la base de datos dentro del servidor. | `studyfinder` |
| `CORS_ORIGINS` | URLs del frontend autorizadas a llamar a la API desde el navegador, separadas por comas y sin barra final. En producción, el dominio real del frontend. | `http://localhost:4200,http://127.0.0.1:4200` |
| `UPLOADS_DIR` | Carpeta de los archivos subidos. En Vercel debe ser `/tmp/uploads` (el código es de solo lectura). | `uploads/` en la raíz del backend |

Cada variable está documentada también en `.env.example`.

> `.env` está en `.gitignore`: **nunca** subas credenciales al repositorio.
> El puerto del servidor **no** se lee de `.env`: se indica con `--port` al arrancar uvicorn
> (en un hosting que define `PORT`, usa `--port $PORT`).

### 3. Arrancar el servidor

```bash
uv run uvicorn app.main:app --port 8000 --reload
```

Comprueba que funciona:

- <http://localhost:8000> → `{"message":"Backend de StudyFinder operando correctamente"}`
- <http://localhost:8000/docs> → documentación interactiva (Swagger / OpenAPI).

> Al arrancar, el backend se conecta a MongoDB y crea los índices que necesita
> (`users.email` único y el índice compuesto de `materia_documents`).

---

## Pruebas

Las pruebas usan **mongomock** (MongoDB en memoria): no necesitan base de datos ni `.env`.

```bash
uv run pytest
```

---

## Gestión de dependencias

`pyproject.toml` declara las dependencias y `uv.lock` fija las versiones exactas.
Ambos archivos se versionan.

```bash
uv add <paquete>            # dependencia de producción
uv add --dev <paquete>      # dependencia de desarrollo (pruebas, linters...)
uv remove <paquete>
uv lock --upgrade-package <paquete>   # actualizar un paquete concreto
```

> Si un entorno necesita un `requirements.txt` (por ejemplo, un hosting que solo usa `pip`),
> genéralo desde el lock en vez de mantenerlo a mano:
> `uv export --no-dev --no-hashes -o requirements.txt`.

---

## Despliegue en Vercel

Vercel detecta FastAPI automáticamente en `app/main.py` (variable `app`) e instala las
dependencias desde `pyproject.toml` + `uv.lock`. No hace falta `vercel.json`.

1. **Rama**: en *Settings → Git → Production Branch*, comprueba qué rama se despliega.
   Debe contener `pyproject.toml` y `.python-version`; si no, Vercel usa Python 3.12 por defecto.
2. **Variables de entorno** (*Settings → Environment Variables*):

   | Variable | Valor en Vercel |
   | --- | --- |
   | `MONGODB_URL` | Cadena de Atlas (`mongodb+srv://...`) |
   | `MONGODB_DATABASE` | `studyfinder` |
   | `CORS_ORIGINS` | URL del frontend desplegado, p. ej. `https://studyfinder-front.vercel.app` |
   | `UPLOADS_DIR` | `/tmp/uploads` |

3. **Atlas**: en *Network Access* permite `0.0.0.0/0`, porque las funciones de Vercel no tienen
   IP fija.
4. Vuelve a desplegar (*Redeploy*) tras cambiar variables.

> **Limitación conocida**: en Vercel `/tmp` es efímero y no se comparte entre instancias.
> Los documentos y adjuntos subidos pueden desaparecer o dar 404 al descargarlos desde otra
> instancia. Para producción hay que mover los archivos a un almacenamiento persistente
> (MongoDB GridFS o Vercel Blob).

---

## Migrar datos antiguos (SQLite → MongoDB)

Si tienes un `studyfinder.db` antiguo en la raíz del backend:

```bash
uv run python -m scripts.migrate_sqlite_to_mongo
```

Es idempotente: los correos ya registrados en MongoDB se saltan.

---

## Estructura

```text
StudyFinder-backend/
├── app/
│   ├── main.py            # Punto de entrada FastAPI, CORS y routers
│   ├── config.py          # Lectura de variables de entorno (.env)
│   ├── database.py        # Conexión a MongoDB, índices y acceso a datos
│   ├── deps.py            # Dependencia de autenticación (Bearer token)
│   ├── api/               # Rutas: auth, users, contacts, documents, messages, search
│   ├── clients/           # Cliente HTTP de OpenAlex
│   └── services/          # Búsqueda de documentos académicos
├── scripts/               # Migración de datos desde SQLite
├── tests/                 # Pruebas (pytest + mongomock)
├── pyproject.toml         # Dependencias y configuración del proyecto
├── uv.lock                # Versiones bloqueadas
├── .python-version        # Python 3.13
└── .env.example           # Plantilla de configuración
```

Los archivos subidos se guardan en disco en `uploads/<user_id>/` (ignorado por Git);
MongoDB solo guarda sus metadatos.

---

## Problemas frecuentes

**`uv` no se reconoce** — reinicia la terminal tras instalarlo, o ejecuta `uv python update-shell`.

**Error al conectar con MongoDB al arrancar** — revisa que `.env` exista, que `MONGODB_URL`
sea correcta y que tu IP esté permitida en *Network Access* de Atlas.

**El frontend no puede llamar a la API (error CORS)** — añade la URL exacta del frontend a
`CORS_ORIGINS` en `.env` (por defecto solo `http://localhost:4200` y `http://127.0.0.1:4200`)
y reinicia el backend.

**PowerShell bloquea scripts** — ejecuta `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
