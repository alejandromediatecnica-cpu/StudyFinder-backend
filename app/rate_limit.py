"""
Limitador de intentos en memoria (ventana deslizante).

Se usa para frenar fuerza bruta en login, signup, verificación de contraseña,
cambio de contraseña y borrado de cuenta. Es suficiente para una sola instancia.

LIMITACIÓN: al estar en memoria, no se comparte entre múltiples procesos o
instancias (en ese caso habría que usar Redis).

CÓMO CAMBIAR UN LÍMITE:
  - No se cambia aquí: cada endpoint llama a `check_rate_limit(key, limite,
    ventana_segundos)` con sus propios valores. Busca `check_rate_limit(` en
    `app/api/`.
CÓMO REINICIAR LOS CONTADORES:
  - `reset_rate_limits()`; los tests lo usan entre casos.
"""

import threading
import time

# Estructura en memoria: clave (p. ej. "login:email:host") -> lista de timestamps.
_attempts: dict[str, list[float]] = {}
# `threading.Lock` protege `_attempts` porque FastAPI puede atender peticiones en
# varios hilos (los endpoints son síncronos).
_lock = threading.Lock()


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Registra un intento y dice si está permitido.

    Lógica (ventana deslizante):
      1. Descarta los timestamps más antiguos que `window_seconds`.
      2. Si quedan `limit` o más, rechaza (False) sin añadir el intento actual.
      3. Si hay hueco, añade el timestamp actual y acepta (True).

    PARÁMETROS:
      - key: identificador del cubo (usuario, email, IP...).
      - limit: número máximo de intentos dentro de la ventana.
      - window_seconds: tamaño de la ventana en segundos.
    RETORNO: True si el intento se permite; False si se superó el límite.
    EFECTOS: muta `_attempts`; usa `time.monotonic()` (no se ve afectado por
    cambios del reloj del sistema).
    """
    now = time.monotonic()
    with _lock:
        # Conserva solo los intentos dentro de la ventana.
        timestamps = [value for value in _attempts.get(key, []) if now - value < window_seconds]
        # Límite alcanzado: no se registra el intento actual.
        if len(timestamps) >= limit:
            _attempts[key] = timestamps
            return False
        # Hay hueco: registra y permite.
        timestamps.append(now)
        _attempts[key] = timestamps
        return True


def reset_rate_limits() -> None:
    """Vacía todos los contadores.

    Efecto: elimina `_attempts` bajo lock. Pensado para tests (fixture autouse en
    `tests/conftest.py`).
    """
    with _lock:
        _attempts.clear()
