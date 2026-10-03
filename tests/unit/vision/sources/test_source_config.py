from pathlib import Path

import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError

from vision_hub.vision.sources import (
    RtspSourceConfig,
    SourceConfig,
    SyntheticSourceConfig,
    VideoFileSourceConfig,
    WebcamSourceConfig,
    create_source,
)
from vision_hub.vision.sources.opencv import RtspSource, VideoFileSource, WebcamSource
from vision_hub.vision.sources.synthetic import SyntheticSource

adapter: TypeAdapter[SourceConfig] = TypeAdapter(SourceConfig)


class TestDiscriminatedUnion:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"kind": "webcam", "index": 1}, WebcamSourceConfig),
            ({"kind": "rtsp", "url": "rtsp://cam.local/stream"}, RtspSourceConfig),
            ({"kind": "video_file", "path": "demo.mp4"}, VideoFileSourceConfig),
            ({"kind": "synthetic"}, SyntheticSourceConfig),
        ],
    )
    def test_kind_selects_the_model(self, data: dict[str, object], expected: type) -> None:
        assert isinstance(adapter.validate_python(data), expected)

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="union_tag_invalid"):
            adapter.validate_python({"kind": "carrier-pigeon"})

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            adapter.validate_python({"kind": "webcam", "idx": 1})


class TestRtspConfig:
    @pytest.mark.parametrize(
        ("url", "error"),
        [
            ("http://cam.local/stream", "rtsp://"),
            ("rtsp://", "rtsp://"),
            ("rtsp://admin:hunter2@cam.local/stream", "must not contain credentials"),
        ],
    )
    def test_url_is_validated(self, url: str, error: str) -> None:
        with pytest.raises(ValidationError, match=error):
            RtspSourceConfig(url=url)

    def test_credentials_leak_into_no_error_message(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            RtspSourceConfig(url="rtsp://admin:hunter2@cam.local/stream")

        assert "hunter2" not in str(exc_info.value)

    def test_password_requires_username(self) -> None:
        with pytest.raises(ValidationError, match="requires a username"):
            RtspSourceConfig(url="rtsp://cam.local/s", password=SecretStr("pw"))

    def test_connection_url_inserts_quoted_credentials(self) -> None:
        config = RtspSourceConfig(
            url="rtsps://cam.local:8554/live?ch=1",
            username="admin@home",
            password=SecretStr("p@ss:w/rd"),
        )

        assert (
            config.connection_url().get_secret_value()
            == "rtsps://admin%40home:p%40ss%3Aw%2Frd@cam.local:8554/live?ch=1"
        )

    def test_connection_url_without_credentials_is_the_url(self) -> None:
        config = RtspSourceConfig(url="rtsp://cam.local/s", username="viewer")

        assert config.connection_url().get_secret_value() == "rtsp://viewer@cam.local/s"
        assert RtspSourceConfig(url="rtsp://cam.local/s").connection_url().get_secret_value() == (
            "rtsp://cam.local/s"
        )

    def test_password_is_masked_everywhere(self) -> None:
        config = RtspSourceConfig(
            url="rtsp://cam.local/s", username="admin", password=SecretStr("hunter2")
        )

        rendered = " ".join([repr(config), str(config), config.model_dump_json()])

        assert "hunter2" not in rendered
        assert "hunter2" not in str(config.connection_url())


def test_synthetic_visit_must_fit_in_its_period() -> None:
    with pytest.raises(ValidationError, match="shorter than"):
        SyntheticSourceConfig(visit_every_seconds=5, visit_seconds=5)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (WebcamSourceConfig(), WebcamSource),
        (RtspSourceConfig(url="rtsp://cam.local/s"), RtspSource),
        (VideoFileSourceConfig(path=Path("demo.mp4")), VideoFileSource),
        (SyntheticSourceConfig(), SyntheticSource),
    ],
)
def test_factory_builds_the_matching_source(config: SourceConfig, expected: type) -> None:
    assert isinstance(create_source(config), expected)
