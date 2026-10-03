"""Device definitions in the database. Camera passwords are stored encrypted, in their own
column, and never inside the JSON settings."""

from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr
from sqlalchemy import delete, select

from vision_hub.core.encryption import SecretBox
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import DeviceRow
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.sources import RtspSourceConfig


class SqlDeviceRepository:
    def __init__(self, sessions: Sessions, secrets: SecretBox) -> None:
        self._sessions = sessions
        self._secrets = secrets

    async def list(self) -> list[DeviceSpec]:
        async with self._sessions() as session:
            rows = await session.scalars(select(DeviceRow).order_by(DeviceRow.id))
            return [self._to_spec(row) for row in rows]

    async def get(self, device_id: str) -> DeviceSpec | None:
        async with self._sessions() as session:
            row = await session.get(DeviceRow, device_id)
        return self._to_spec(row) if row else None

    async def save(self, device: DeviceSpec) -> None:
        values = self._to_values(device)
        async with self._sessions.begin() as session:
            row = await session.get(DeviceRow, device.id)
            if row is None:
                session.add(DeviceRow(id=device.id, **values))
            else:
                for column, value in values.items():
                    setattr(row, column, value)

    async def add_missing(self, devices: Sequence[DeviceSpec]) -> tuple[str, ...]:
        """Seed from the fleet file: insert devices the database does not know yet."""
        added: tuple[str, ...] = ()
        for device in devices:
            if await self.get(device.id) is None:
                await self.save(device)
                added += (device.id,)
        return added

    async def delete(self, device_id: str) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(delete(DeviceRow).where(DeviceRow.id == device_id))
        return bool(getattr(result, "rowcount", 0))

    def _to_values(self, device: DeviceSpec) -> dict[str, Any]:
        source = device.source
        secret = None
        if isinstance(source, RtspSourceConfig) and source.password is not None:
            secret = self._secrets.encrypt(source.password.get_secret_value())
        return {
            "name": device.name,
            "enabled": device.enabled,
            "target_fps": device.target_fps,
            "source_kind": source.kind,
            # The password is excluded from serialization by the model itself.
            "source": source.model_dump(mode="json", exclude={"has_password"}),
            "source_secret": secret,
            "detection": device.detection.model_dump(mode="json"),
        }

    def _to_spec(self, row: DeviceRow) -> DeviceSpec:
        source: dict[str, Any] = dict(row.source)
        if row.source_secret is not None:
            source["password"] = SecretStr(self._secrets.decrypt(row.source_secret))
        return DeviceSpec.model_validate(
            {
                "id": row.id,
                "name": row.name,
                "enabled": row.enabled,
                "target_fps": row.target_fps,
                "source": source,
                "detection": row.detection,
            }
        )
