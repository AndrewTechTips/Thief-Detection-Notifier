from vision_hub.domain.devices import DeviceStatus, SourceKind


def test_enums_serialise_as_plain_strings() -> None:
    assert SourceKind.VIDEO_FILE.value == "video_file"
    assert str(DeviceStatus.RECONNECTING) == "reconnecting"
