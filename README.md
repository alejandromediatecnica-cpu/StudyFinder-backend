# StudyFinder — Backend

API REST de **StudyFinder** hecha con **FastAPI** (Python 3.13). Guarda usuarios, sesiones,
documentos, contactos y mensajes en **MongoDB** (driver `pymongo`) y busca artículos
académicos en **OpenAlex**. Escucha en `http://localhost:8000`.

El frontend (Angular) vive en su propio repositorio, `StudyFinder-Frontend`, y necesita
este backend encendido para funcionar.

---

## Requisitos previos

| Herramienta | Versión | Para qué sirve | Comprobar |
|---|---|---|---|
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
|---|---|---|
| `MONGODB_URL` | Cadena de conexión a MongoDB | `mongodb://localhost:27017` |
| `MONGODB_DATABASE` | Nombre de la base de datos | `studyfinder` |

> `.env` está en `.gitignore`: **nunca** subas credenciales al repositorio.

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

> `requirements.txt` y `requirements-dev.txt` se mantienen temporalmente para los entornos
> que todavía instalan con `pip`. Se eliminarán cuando la migración a `uv` termine.
> Fuente de verdad: `pyproject.toml` + `uv.lock`.

---

## Migrar datos antiguos (SQLite → MongoDB)

Si tienes un `studyfinder.db` antiguo en la raíz del backend:

```bash
uv run python -m scripts.migrate_sqlite_to_mongo
```

Es idempotente: los correos ya registrados en MongoDB se saltan.

---

## Estructura

```
StudyFinder-backend/
├── app/
│   ├── main.py            # Punto de entrada FastAPI, CORS y routers
│   ├── config.py          # Lectura de variables de entorno
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

**El frontend no puede llamar a la API (error CORS)** — el backend solo acepta peticiones
desde `http://localhost:4200` y `http://127.0.0.1:4200`. Arranca el frontend en ese puerto.

**PowerShell bloquea scripts** — ejecuta `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
