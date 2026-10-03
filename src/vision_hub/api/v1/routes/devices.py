"""Camera management. Reading needs any authenticated user; changes need the admin role."""

from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import StreamingResponse

from vision_hub.api.deps import AdminOnly, DeviceServiceDep, SettingsDep
from vision_hub.api.v1 import API_V1_PREFIX
from vision_hub.realtime.mjpeg import MEDIA_TYPE, mjpeg_stream
from vision_hub.schemas.devices import (
    DeviceCreate,
    DeviceOut,
    DeviceUpdate,
    SourceTestRequest,
    SourceTestResult,
)
from vision_hub.schemas.pagination import Page, PageParamsDep, encode_cursor
from vision_hub.schemas.problem import ProblemDetail
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceSpec

type _Responses = dict[int | str, dict[str, Any]]


def _problems(*codes: int) -> _Responses:
    return {code: {"model": ProblemDetail} for code in codes}


router = APIRouter(prefix="/devices", tags=["devices"], responses=_problems(403))


@router.get("", summary="List devices", responses=_problems(400))
async def list_devices(devices: DeviceServiceDep, page: PageParamsDep) -> Page[DeviceOut]:
    after = str(page.cursor.get("after", "")) if page.cursor else ""
    views = [view for view in await devices.list() if view.spec.id > after]
    items = views[: page.limit]
    has_more = len(views) > page.limit
    return Page(
        items=[DeviceOut.from_view(view) for view in items],
        next_cursor=encode_cursor({"after": items[-1].spec.id}) if has_more else None,
    )


@router.post(
    "",
    summary="Add a device",
    dependencies=[AdminOnly],
    status_code=status.HTTP_201_CREATED,
    responses=_problems(400, 409),
)
async def create_device(
    body: DeviceCreate, response: Response, devices: DeviceServiceDep
) -> DeviceOut:
    """Starts the camera right away when `enabled`. Changes last until the hub restarts (the
    devices file is loaded at startup); persistent storage arrives with the database."""
    spec = DeviceSpec(
        id=body.id,
        name=body.name,
        enabled=body.enabled,
        target_fps=body.target_fps,
        retention_days=body.retention_days,
        source=body.source,
        detection=body.detection or devices.detection_defaults,
    )
    view = await devices.create(spec)
    response.headers["Location"] = f"{API_V1_PREFIX}/devices/{view.spec.id}"
    return DeviceOut.from_view(view)


@router.post("/test", summary="Test a source", dependencies=[AdminOnly], responses=_problems(400))
async def check_source(body: SourceTestRequest, devices: DeviceServiceDep) -> SourceTestResult:
    """Open the source and read one frame, without saving anything. A failure is reported in
    the body (`ok: false`), not as an HTTP error."""
    return SourceTestResult.model_validate(await devices.test_source(body.source))


@router.get("/{device_id}", summary="Get a device", responses=_problems(404))
async def get_device(device_id: str, devices: DeviceServiceDep) -> DeviceOut:
    return DeviceOut.from_view(await devices.get(device_id))


@router.patch(
    "/{device_id}",
    summary="Update a device",
    dependencies=[AdminOnly],
    responses=_problems(400, 404),
)
async def update_device(device_id: str, body: DeviceUpdate, devices: DeviceServiceDep) -> DeviceOut:
    """Only fields present in the body change. Changing the source, detection or frame rate
    restarts a running camera."""
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    return DeviceOut.from_view(await devices.update(device_id, changes))


@router.delete(
    "/{device_id}",
    summary="Delete a device",
    dependencies=[AdminOnly],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_problems(404),
)
async def delete_device(device_id: str, devices: DeviceServiceDep) -> None:
    await devices.delete(device_id)


@router.post(
    "/{device_id}/start",
    summary="Start a camera",
    dependencies=[AdminOnly],
    status_code=status.HTTP_202_ACCEPTED,
    responses=_problems(404),
)
async def start_device(device_id: str, devices: DeviceServiceDep) -> DeviceOut:
    """Accepted: the camera connects in the background; watch `status` become `online`."""
    return DeviceOut.from_view(await devices.start(device_id))


@router.post(
    "/{device_id}/stop", summary="Stop a camera", dependencies=[AdminOnly], responses=_problems(404)
)
async def stop_device(device_id: str, devices: DeviceServiceDep) -> DeviceOut:
    return DeviceOut.from_view(await devices.stop(device_id))


@router.put(
    "/{device_id}/detection-config",
    summary="Replace detection settings",
    dependencies=[AdminOnly],
    responses=_problems(404),
)
async def set_detection_config(
    device_id: str, body: DetectionConfig, devices: DeviceServiceDep
) -> DeviceOut:
    """Hot reload: a running camera restarts with the new settings (its background model
    re-learns for a moment)."""
    return DeviceOut.from_view(await devices.set_detection(device_id, body))


@router.get(
    "/{device_id}/snapshot",
    summary="Latest frame",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "JPEG of the latest live frame"},
        **_problems(404, 503),
    },
)
async def snapshot(device_id: str, devices: DeviceServiceDep) -> Response:
    """At most about a second old (live frames refresh continuously while someone watches)."""
    frame = await devices.snapshot(device_id)
    return Response(
        frame.jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Captured-At": frame.captured_at.isoformat(),
        },
    )


# Mounted on the ticket-or-bearer router: <img src> cannot send an Authorization header.
stream_router = APIRouter(prefix="/devices", tags=["devices"])


@stream_router.get(
    "/{device_id}/stream",
    summary="Live MJPEG stream",
    response_class=StreamingResponse,
    responses={
        200: {"content": {MEDIA_TYPE: {}}, "description": "One JPEG per multipart part"},
        **_problems(401, 404, 503),
    },
)
async def stream(
    device_id: str,
    devices: DeviceServiceDep,
    settings: SettingsDep,
    fps: Annotated[float | None, Query(gt=0, le=60, description="Frame rate cap")] = None,
) -> StreamingResponse:
    """Use directly as `<img src=".../stream?ticket=...">`. Ends when the camera stops."""
    frames = await devices.live_frames(device_id)
    max_fps = min(fps or settings.realtime.stream_max_fps, settings.realtime.stream_max_fps)
    return StreamingResponse(
        mjpeg_stream(frames, max_fps=max_fps, still_running=lambda: devices.is_running(device_id)),
        media_type=MEDIA_TYPE,
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
