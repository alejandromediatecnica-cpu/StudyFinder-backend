"""Limitador de intentos en memoria.

Suficiente para una instancia única. En un despliegue con varias instancias
conviene sustituirlo por Redis, pero evita los ataques de fuerza bruta básicos
sin añadir infraestructura al proyecto.
"""

import threading
import time

_attempts: dict[str, list[float]] = {}
_lock = threading.Lock()


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _lock:
        timestamps = [value for value in _attempts.get(key, []) if now - value < window_seconds]
        if len(timestamps) >= limit:
            _attempts[key] = timestamps
            return False
        timestamps.append(now)
        _attempts[key] = timestamps
        return True


def reset_rate_limits() -> None:
    with _lock:
        _attempts.clear()
