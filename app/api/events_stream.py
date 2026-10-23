"""
Stream de eventos en tiempo real (Server-Sent Events).

CONTRATO CON EL FRONTEND (`RealtimeService`)
  - Conexión: `GET /events/stream?token=<token>` (o cabecera Authorization).
  - Primer mensaje: `event: ready` con `data: {}` (el cliente lo usa como señal
    de conexión establecida).
  - Eventos: `event: update` + `data: <json>` con
    `{type, entity, action, data}`.
  - Cada 15 s sin eventos se envía un comentario `: latido` para que proxies y
    navegadores no cierren la conexión por inactividad.

POR QUÉ EL TOKEN VA EN LA QUERY
  `EventSource` del navegador no permite enviar cabeceras personalizadas; por eso
  se acepta `?token=`. Pega: el token puede quedar en logs del servidor. Si
  migras a `fetch` con streaming, usa la cabecera y elimina el parámetro.

CÓMO CAMBIAR LA FRECUENCIA DEL LATIDO
  - `HEARTBEAT_SECONDS`.
CÓMO ENVIAR UN EVENTO NUEVO
  - No se hace aquí: los routers llaman a `notify(...)` (`app/realtime.py`).
"""

import asyncio

from fastapi import APIRouter, HTTPException, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from app.database import user_id_for_token
from app.realtime import bus

router = APIRouter(prefix="/events", tags=["events"])

# Segundos sin eventos antes de mandar un comentario de keep-alive.
HEARTBEAT_SECONDS = 15


def _authenticated_user_id(token: str | None, authorization: str | None) -> str:
    """Resuelve el usuario por query token o por cabecera Bearer.

    Prioridad: `?token=` y, si no, `Authorization: Bearer`. 401 si ninguno es
    válido.
    """
    if token:
        user_id = user_id_for_token(token)
    elif authorization and authorization.startswith("Bearer "):
        user_id = user_id_for_token(authorization.removeprefix("Bearer ").strip())
    else:
        user_id = None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión ya no es válida. Inicia sesión de nuevo.",
        )
    return user_id


@router.get("/stream")
async def event_stream(
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    """Abre el stream SSE del usuario autenticado.

    Flujo:
      1. Autentica y se suscribe al bus (cola propia de esta conexión).
      2. Generador asíncrono:
         - envía `ready`;
         - espera un evento de la cola con timeout de latido;
         - si hay evento -> `event: update`;
         - si hay timeout -> comentario `: latido`;
         - si el cliente se desconecta -> sale del bucle.
      3. `finally`: se desuscribe para no filtrar memoria.

    EFECTOS: mantiene la conexión abierta; lee de una `asyncio.Queue`.
    CABECERAS: `no-cache`, `keep-alive` y `X-Accel-Buffering: no` (evita que
    Nginx/Cloudflare acumulen el stream).
    """
    user_id = _authenticated_user_id(token, authorization)
    queue = bus.subscribe(user_id)

    async def generator():
        try:
            # Señal de conexión lista (el cliente puede marcar "en línea").
            yield "event: ready\ndata: {}\n\n"
            while True:
                # Salida limpia si el navegador cerró la pestaña.
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Comentario SSE: mantiene viva la conexión sin disparar eventos.
                    yield ": latido\n\n"
                    continue
                yield f"event: update\ndata: {payload}\n\n"
        finally:
            # Siempre se libera la suscripción, también ante excepciones.
            bus.unsubscribe(user_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
