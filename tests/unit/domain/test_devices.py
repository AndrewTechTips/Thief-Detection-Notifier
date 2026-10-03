from vision_hub.domain.devices import Device, DeviceStatus, SourceKind


def test_new_devices_are_enabled_and_stopped() -> None:
    device = Device(id="cam-1", name="Porch", source_kind=SourceKind.RTSP)

    assert device.enabled is True
    assert device.status is DeviceStatus.STOPPED


def test_enums_serialise_as_plain_strings() -> None:
    assert SourceKind.VIDEO_FILE.value == "video_file"
    assert str(DeviceStatus.RECONNECTING) == "reconnecting"
