"""
Bus de eventos en memoria para Server-Sent Events (SSE).

Cómo funciona el tiempo real:
  1. El frontend abre `GET /events/stream?token=...` (ver `app/api/events_stream.py`).
  2. `event_stream` llama a `bus.subscribe(user_id)` y obtiene una `asyncio.Queue`.
  3. Cuando un router escribe en Mongo, llama a `notify(...)`, que publica un
     evento en las colas de los usuarios afectados.
  4. El generador SSE despierta, formatea `event: update` y envía el JSON.

IMPORTANTE: los eventos se publican DESPUÉS de confirmar la escritura en Mongo,
así el cliente nunca recibe como exitoso un cambio que no se persistió.

LIMITACIÓN: en memoria, una sola instancia. Con varias instancias haría falta
Redis pub/sub u otro bus compartido.

CÓMO AÑADIR UN EVENTO NUEVO:
  - Desde el router, tras escribir en Mongo: `notify([user_id], "miEntidad",
    "created|updated|deleted", payload)`.
CÓMO CAMBIAR EL NOMBRE DE LA ENTIDAD:
  - Usa siempre el PLURAL y el mismo string que filtra el frontend
    (`tasks`, `calendar`, `documents`, `messages`, `contacts`, `profile`).
"""

import asyncio
import json
import threading
from collections.abc import Iterable

# Tamaño máximo de la cola por conexión. Si el cliente es lento y se llena, se
# descarta el evento más antiguo (ver `_enqueue`) para no bloquear al emisor.
_QUEUE_SIZE = 100


class EventBus:
    """Registro de conexiones SSE agrupadas por `user_id`.

    Estructura interna:
      `_subscribers[user_id][id(queue)] = (queue, loop_de_ese_cliente)`
    Se guarda el `loop` porque `publish` puede llamarse desde un hilo distinto al
    del bucle asyncio de cada cliente; `call_soon_threadsafe` lo reinyecta.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, dict[int, tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = {}
        # Protege `_subscribers` frente a accesos concurrentes (hilos).
        self._lock = threading.Lock()

    def subscribe(self, user_id: str) -> asyncio.Queue:
        """Registra una conexión SSE y devuelve su cola de eventos.

        Debe llamarse dentro del bucle asyncio de la petición (usa
        `get_running_loop`). Un mismo usuario puede tener varias conexiones
        (varias pestañas/dispositivos); se guardan todas.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.setdefault(user_id, {})[id(queue)] = (queue, loop)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        """Elimina una conexión concreta.

        Se identifica la conexión por `id(queue)`. Si el usuario se queda sin
        conexiones, se elimina su entrada para no acumular memoria.
        """
        with self._lock:
            connections = self._subscribers.get(user_id)
            if not connections:
                return
            connections.pop(id(queue), None)
            if not connections:
                self._subscribers.pop(user_id, None)

    def publish(self, user_ids: Iterable[str], event: dict) -> None:
        """Envía `event` a todas las conexiones de los usuarios indicados.

        Pasos:
          1. Normaliza y deduplica los destinatarios.
          2. Serializa el evento a JSON (UTF-8, no-ASCII permitido).
          3. Copia la lista de conexiones bajo lock.
          4. Encola en el bucle de cada cliente con `call_soon_threadsafe`.

        EFECTOS: escribe en las colas de los clientes; no toca la base de datos.
        Si el bucle de un cliente ya se cerró (`RuntimeError`), se ignora.
        """
        recipients = {str(user_id) for user_id in user_ids if user_id}
        if not recipients:
            return
        payload = json.dumps(event, ensure_ascii=False, default=str)
        with self._lock:
            targets = [
                connection
                for user_id in recipients
                for connection in self._subscribers.get(user_id, {}).values()
            ]
        for queue, loop in targets:
            try:
                loop.call_soon_threadsafe(_enqueue, queue, payload)
            except RuntimeError:
                # El bucle ya se cerró (cliente desconectado).
                continue


def _enqueue(queue: asyncio.Queue, payload: str) -> None:
    """Encola un payload sin bloquear; si la cola está llena, tira el más viejo.

    Esto evita que un cliente lento (que no lee el stream) bloquee al emisor.
    Debe ejecutarse en el bucle del cliente (lo garantiza `call_soon_threadsafe`).
    """
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(payload)


# Instancia global compartida por toda la app.
bus = EventBus()


def notify(user_ids: Iterable[str], entity: str, action: str, data: dict | None = None) -> None:
    """Publica un evento con el formato que espera el frontend.

    Construye `{"type": f"{entity}.{action}", "entity", "action", "data"}` y lo
    entrega al bus. Es la función que llaman los routers tras escribir en Mongo.

    PARÁMETROS:
      - user_ids: a quién notificar (normalmente el dueño y, en chat/contactos,
        ambos usuarios).
      - entity: nombre en PLURAL (`tasks`, `calendar`, `documents`, `messages`,
        `contacts`, `profile`).
      - action: `created`, `updated` o `deleted`.
      - data: payload opcional; por defecto `{}`.
    """
    bus.publish(
        user_ids,
        {
            "type": f"{entity}.{action}",
            "entity": entity,
            "action": action,
            "data": data or {},
        },
    )
