import asyncio

from fastapi import APIRouter, HTTPException, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from app.database import user_id_for_token
from app.realtime import bus

router = APIRouter(prefix="/events", tags=["events"])

HEARTBEAT_SECONDS = 15


def _authenticated_user_id(token: str | None, authorization: str | None) -> str:
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
    user_id = _authenticated_user_id(token, authorization)
    queue = bus.subscribe(user_id)

    async def generator():
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": latido\n\n"
                    continue
                yield f"event: update\ndata: {payload}\n\n"
        finally:
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
