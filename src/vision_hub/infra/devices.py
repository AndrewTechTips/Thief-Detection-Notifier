"""In-memory device repository: seeded from the fleet file, changes last until restart.
Phase 3 replaces it with a database-backed implementation of the same port."""

from collections.abc import Iterable

from vision_hub.vision.fleet import DeviceSpec


class InMemoryDeviceRepository:
    def __init__(self, devices: Iterable[DeviceSpec] = ()) -> None:
        self._devices = {device.id: device for device in devices}

    async def list(self) -> list[DeviceSpec]:
        return sorted(self._devices.values(), key=lambda device: device.id)

    async def get(self, device_id: str) -> DeviceSpec | None:
        return self._devices.get(device_id)

    async def save(self, device: DeviceSpec) -> None:
        self._devices[device.id] = device

    async def delete(self, device_id: str) -> bool:
        return self._devices.pop(device_id, None) is not None
