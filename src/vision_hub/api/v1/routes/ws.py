"""WebSocket endpoint for live events. Protocol: ``vision_hub.schemas.ws``."""

from typing import Annotated

from fastapi import APIRouter, Query, WebSocket

from vision_hub.api.deps import get_container
from vision_hub.core.logging import get_logger
from vision_hub.realtime.connections import CloseCode

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/events")
async def events(
    websocket: WebSocket, ticket: Annotated[str | None, Query(max_length=128)] = None
) -> None:
    """Authenticate with a single-use ticket *before* accepting, then stream events."""
    container = get_container(websocket)
    principal = container.tickets.consume(ticket) if ticket else None
    if principal is None:
        logger.warning("ws_rejected", reason="missing ticket" if not ticket else "invalid ticket")
        await websocket.close(code=CloseCode.UNAUTHORIZED, reason="invalid or missing ticket")
        return
    await websocket.accept()
    await container.realtime.serve(websocket, principal)
