from datetime import datetime

from pydantic import Field

from vision_hub.core.security import UrlSigner
from vision_hub.domain.history import EventRecord
from vision_hub.domain.motion import BoundingBox
from vision_hub.domain.storage import SnapshotKind
from vision_hub.schemas.base import ApiSchema


class SnapshotLink(ApiSchema):
    kind: SnapshotKind
    url: str = Field(description="Signed, expiring link: usable directly in an <img> tag")
    size_bytes: int


class EventOut(ApiSchema):
    id: str
    device_id: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    complete: bool = Field(description="True once the event has ended")
    interrupted: bool = Field(description="The hub stopped abruptly before the event ended")
    peak_area_ratio: float = Field(description="Largest moving region, as a fraction of the frame")
    motion_frames: int
    boxes: list[BoundingBox]
    snapshots: list[SnapshotLink]

    @classmethod
    def from_record(cls, record: EventRecord, signer: UrlSigner, base_path: str) -> EventOut:
        event = record.event
        links = []
        for kind, snapshot in sorted(record.snapshots.items()):
            resource = snapshot_resource(base_path, event.id, kind)
            expires, signature = signer.sign(resource)
            links.append(
                SnapshotLink(
                    kind=kind,
                    url=f"{resource}&expires={expires}&signature={signature}",
                    size_bytes=snapshot.size_bytes,
                )
            )
        duration = (event.ended_at - event.started_at).total_seconds() if event.ended_at else None
        return cls(
            id=event.id,
            device_id=event.device_id,
            started_at=event.started_at,
            ended_at=event.ended_at,
            duration_seconds=duration,
            complete=record.complete,
            interrupted=record.interrupted,
            peak_area_ratio=event.peak_area_ratio,
            motion_frames=event.motion_frames,
            boxes=list(record.boxes),
            snapshots=links,
        )


def snapshot_resource(base_path: str, event_id: str, kind: SnapshotKind) -> str:
    """The signed part of a snapshot URL: path plus the kind it is bound to."""
    return f"{base_path}/events/{event_id}/snapshot?kind={kind}"
