import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr

from vision_hub.core.config import SmtpConfig
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.notifications import Alert, NotificationError
from vision_hub.infra.notifiers.email import EmailNotifier

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"
STARTED = datetime(2026, 10, 3, 22, 14, 5, tzinfo=UTC)
ALERT = Alert(
    device_id="porch",
    device_name="Front porch",
    event=MotionEvent(
        id="01a1-event",
        device_id="porch",
        started_at=STARTED,
        ended_at=STARTED + timedelta(seconds=4.5),
        peak_area_ratio=0.08,
        motion_frames=40,
    ),
    image_jpeg=JPEG,
)


def smtp_config(port: int, **overrides: Any) -> SmtpConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "host": "127.0.0.1",
        "port": port,
        "tls": "none",
        "timeout_seconds": 2,
        "username": "alerts@example.com",
        "password": SecretStr("app-password"),
        "recipients": ["me@example.com", "partner@example.com"],
    }
    return SmtpConfig.model_validate(values | overrides)


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


class TestMessage:
    def test_has_subject_addresses_and_event_header(self) -> None:
        message = EmailNotifier(smtp_config(25)).build_message(ALERT)

        assert message["Subject"] == "Motion detected: Front porch"
        assert message["From"] == "alerts@example.com"
        assert message["To"] == "me@example.com, partner@example.com"
        assert message["X-Vision-Hub-Event"] == "01a1-event"

    def test_says_person_when_one_was_seen(self) -> None:
        seen = replace(ALERT, event=replace(ALERT.event, person=True, person_confidence=0.9))

        message = EmailNotifier(smtp_config(25)).build_message(seen)

        assert message["Subject"] == "Person detected: Front porch"
        text = message.get_body(preferencelist=("plain",))
        assert text is not None
        assert "Person was detected by Front porch" in text.get_content()

    def test_text_body_summarises_the_event(self) -> None:
        message = EmailNotifier(smtp_config(25)).build_message(ALERT)

        text = message.get_body(preferencelist=("plain",))
        assert text is not None
        body = text.get_content()
        assert "Front porch at 2026-10-03 22:14:05 UTC" in body
        assert "lasted 4.5 s" in body

    def test_html_body_shows_the_snapshot_inline(self) -> None:
        message = EmailNotifier(smtp_config(25)).build_message(ALERT)

        html = message.get_body(preferencelist=("html",))
        assert html is not None
        [inline] = [part for part in message.walk() if part.get("Content-ID")]
        cid = inline["Content-ID"].strip("<>")
        assert f'src="cid:{cid}"' in html.get_content()
        assert inline.get_content() == JPEG

    def test_snapshot_is_also_attached(self) -> None:
        message = EmailNotifier(smtp_config(25)).build_message(ALERT)

        [attachment] = list(message.iter_attachments())
        assert attachment.get_filename() == "motion-porch-20261003T221405Z.jpg"
        assert attachment.get_content_type() == "image/jpeg"
        assert attachment.get_content() == JPEG

    def test_missing_snapshot_sends_text_only(self) -> None:
        alert = Alert(ALERT.device_id, ALERT.device_name, ALERT.event, image_jpeg=b"")

        message = EmailNotifier(smtp_config(25)).build_message(alert)

        assert list(message.iter_attachments()) == []
        assert message.get_body(preferencelist=("html",)) is None
        assert "no longer available" in message.get_content()

    def test_html_is_escaped(self) -> None:
        alert = Alert(
            device_id="x",
            device_name="<script>alert(1)</script>",
            event=ALERT.event,
            image_jpeg=JPEG,
        )

        html = EmailNotifier(smtp_config(25)).build_message(alert).get_body(("html",))

        assert html is not None
        assert "<script>" not in html.get_content()

    def test_requires_enabled_smtp(self) -> None:
        with pytest.raises(ValueError, match="SMTP enabled"):
            EmailNotifier(SmtpConfig())


class TestDelivery:
    async def test_sends_over_smtp_with_authentication(self, smtp_server: Any) -> None:
        await EmailNotifier(smtp_config(smtp_server.port)).send(ALERT)

        [mail] = smtp_server.received
        assert mail.authenticated_as == "alerts@example.com"
        assert mail.sender == "alerts@example.com"
        assert mail.recipients == ["me@example.com", "partner@example.com"]
        [attachment] = list(mail.message.iter_attachments())
        assert attachment.get_content() == JPEG

    async def test_never_does_a_reverse_dns_lookup(
        self, smtp_server: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test: aiosmtplib's default EHLO name comes from socket.getfqdn(), which
        blocked every alert for 5 s on a stock macOS hostname."""

        def no_dns(*_args: object) -> str:
            raise AssertionError("getfqdn must not be called")

        monkeypatch.setattr(socket, "getfqdn", no_dns)

        await EmailNotifier(smtp_config(smtp_server.port)).send(ALERT)

        assert len(smtp_server.received) == 1

    async def test_wrong_password_is_permanent(self, smtp_server: Any) -> None:
        notifier = EmailNotifier(smtp_config(smtp_server.port, password=SecretStr("wrong")))

        with pytest.raises(NotificationError) as exc_info:
            await notifier.send(ALERT)

        assert exc_info.value.retryable is False
        assert "wrong" not in str(exc_info.value)

    async def test_rejected_recipients_are_permanent(self, smtp_server_factory: Any) -> None:
        server = await smtp_server_factory(reject_recipients=True)
        notifier = EmailNotifier(
            smtp_config(server.port, username=None, password=None, sender="a@b.io")
        )

        with pytest.raises(NotificationError) as exc_info:
            await notifier.send(ALERT)

        assert exc_info.value.retryable is False

    async def test_unreachable_server_is_retryable(self) -> None:
        with pytest.raises(NotificationError) as exc_info:
            await EmailNotifier(smtp_config(unused_port())).send(ALERT)

        assert exc_info.value.retryable is True

    async def test_dropped_connection_is_retryable(self, smtp_server_factory: Any) -> None:
        server = await smtp_server_factory(drop_connections=True)

        with pytest.raises(NotificationError) as exc_info:
            await EmailNotifier(smtp_config(server.port)).send(ALERT)

        assert exc_info.value.retryable is True
