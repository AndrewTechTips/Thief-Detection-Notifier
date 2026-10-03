"""Event history: list and inspect recorded motion events, fetch their snapshots."""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from vision_hub.api.deps import ContainerDep, EventServiceDep
from vision_hub.api.v1 import API_V1_PREFIX
from vision_hub.domain.storage import SnapshotKind
from vision_hub.schemas.base import UtcDateTime
from vision_hub.schemas.events import EventOut
from vision_hub.schemas.pagination import Page, PageParamsDep, encode_cursor
from vision_hub.schemas.problem import ProblemDetail

type _Responses = dict[int | str, dict[str, Any]]


def _problems(*codes: int) -> _Responses:
    return {code: {"model": ProblemDetail} for code in codes}


router = APIRouter(prefix="/events", tags=["events"])


@router.get("", summary="List events", responses=_problems(400))
async def list_events(
    events: EventServiceDep,
    container: ContainerDep,
    page: PageParamsDep,
    device_id: Annotated[str | None, Query(max_length=63)] = None,
    since: Annotated[UtcDateTime | None, Query(description="Started at or after (UTC)")] = None,
    until: Annotated[UtcDateTime | None, Query(description="Started before (UTC)")] = None,
) -> Page[EventOut]:
    """Newest first. Snapshot links in the response are signed and expire after
    `VISION_HUB_SECURITY__SIGNED_URL_TTL_SECONDS`."""
    records = await events.list(
        limit=page.limit + 1,
        device_id=device_id,
        since=since,
        until=until,
        before=page.time_position("started_at"),
    )
    items = records[: page.limit]
    next_cursor = None
    if len(records) > page.limit:
        last = items[-1].event
        next_cursor = encode_cursor({"started_at": last.started_at.isoformat(), "id": last.id})
    return Page(
        items=[EventOut.from_record(r, container.url_signer, API_V1_PREFIX) for r in items],
        next_cursor=next_cursor,
    )


@router.get("/{event_id}", summary="Get an event", responses=_problems(404))
async def get_event(event_id: str, events: EventServiceDep, container: ContainerDep) -> EventOut:
    record = await events.get(event_id)
    return EventOut.from_record(record, container.url_signer, API_V1_PREFIX)


# Mounted on the signed-or-bearer router: <img> tags cannot send an Authorization header.
snapshot_router = APIRouter(prefix="/events", tags=["events"])


@snapshot_router.get(
    "/{event_id}/snapshot",
    summary="Event snapshot",
    response_class=FileResponse,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "JPEG"},
        **_problems(401, 404),
    },
)
async def snapshot(
    event_id: str,
    events: EventServiceDep,
    kind: SnapshotKind = SnapshotKind.ANNOTATED,
) -> FileResponse:
    """Use the signed `url` from an event, or a bearer token. Snapshots never change, so
    browsers may cache them privately."""
    path = await events.snapshot_file(event_id, kind)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600, immutable"},
    )
