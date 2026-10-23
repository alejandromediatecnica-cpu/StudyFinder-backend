"""Bus de eventos en memoria para Server-Sent Events.

Cada usuario autenticado puede mantener una o varias conexiones abiertas. Los
eventos se publican después de confirmar la escritura en MongoDB, de modo que
el cliente nunca recibe como exitoso un cambio que no quedó persistido.
"""

import asyncio
import json
import threading
from collections.abc import Iterable

_QUEUE_SIZE = 100


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, dict[int, tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, user_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.setdefault(user_id, {})[id(queue)] = (queue, loop)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            connections = self._subscribers.get(user_id)
            if not connections:
                return
            connections.pop(id(queue), None)
            if not connections:
                self._subscribers.pop(user_id, None)

    def publish(self, user_ids: Iterable[str], event: dict) -> None:
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
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(payload)


bus = EventBus()


def notify(user_ids: Iterable[str], entity: str, action: str, data: dict | None = None) -> None:
    bus.publish(
        user_ids,
        {
            "type": f"{entity}.{action}",
            "entity": entity,
            "action": action,
            "data": data or {},
        },
    )
