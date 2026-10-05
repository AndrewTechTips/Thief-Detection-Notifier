"""Web push: RFC 8291 encryption, VAPID signing, and delivery to many browsers."""

import json
import stat
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from tests.fakes.webpush import NOW, Browser, FakePushService

from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.notifications import Alert, NotificationError
from vision_hub.domain.push import PushSubscription, endpoint_allowed, subscription_id
from vision_hub.infra.notifiers.webpush import (
    PushResponse,
    VapidKey,
    WebPushNotifier,
    b64url_decode,
    b64url_encode,
    encrypt,
    load_or_create_vapid_key,
)


class TestEncryption:
    def test_matches_the_worked_example_in_rfc_8291(self) -> None:
        sender = ec.derive_private_key(
            int.from_bytes(b64url_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw")),
            ec.SECP256R1(),
        )

        body = encrypt(
            b"When I grow up, I want to be a watermelon",
            receiver_key=b64url_decode(
                "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
            ),
            auth_secret=b64url_decode("BTBZMqHH6r4Tts7J_aSIgg"),
            sender_key=sender,
            salt=b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
        )

        assert b64url_encode(body) == (
            "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLo"
            "cInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWL"
            "VWGNWQexSgSxsj_Qulcy4a-fN"
        )

    def test_a_browser_can_decrypt_it(self) -> None:
        browser = Browser()

        body = encrypt(b"hello", receiver_key=browser.public, auth_secret=browser.auth)

        assert browser.decrypt(body) == b"hello"

    def test_every_message_uses_fresh_keys(self) -> None:
        browser = Browser()

        first = encrypt(b"same", receiver_key=browser.public, auth_secret=browser.auth)
        second = encrypt(b"same", receiver_key=browser.public, auth_secret=browser.auth)

        assert first[:16] != second[:16]  # salt
        assert first[21:86] != second[21:86]  # sender key

    def test_refuses_payloads_push_services_may_drop(self) -> None:
        browser = Browser()
        encrypt(b"x" * 3993, receiver_key=browser.public, auth_secret=browser.auth)

        with pytest.raises(ValueError, match="too large"):
            encrypt(b"x" * 3994, receiver_key=browser.public, auth_secret=browser.auth)

    @pytest.mark.parametrize("key", [b"\x04" + b"\x00" * 64, b"\x02" * 33, b"short"])
    def test_rejects_keys_that_are_not_p256_points(self, key: bytes) -> None:
        with pytest.raises(ValueError):  # noqa: PT011 - cryptography's own messages vary
            encrypt(b"hi", receiver_key=key, auth_secret=b"0" * 16)


class TestVapidKey:
    def test_text_form_round_trips(self) -> None:
        key = VapidKey.generate()

        again = VapidKey.from_text(key.to_text())

        assert again.public_key == key.public_key
        assert len(b64url_decode(key.public_key)) == 65

    @pytest.mark.parametrize("text", ["", "abc", b64url_encode(b"x" * 31), "!!!"])
    def test_rejects_malformed_keys(self, text: str) -> None:
        with pytest.raises(ValueError, match="32 bytes"):
            VapidKey.from_text(text)

    def test_authorization_is_a_signed_token_for_the_push_service(self) -> None:
        key = VapidKey.generate()

        header = key.authorization(
            "https://fcm.googleapis.com/fcm/send/abc", subject="mailto:me@example.com", now=NOW
        )

        token, public = header.removeprefix("vapid t=").split(", k=")
        assert public == key.public_key
        verifier = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), b64url_decode(public)
        )
        claims = jwt.decode(
            token,
            verifier,
            algorithms=["ES256"],
            audience="https://fcm.googleapis.com",
            options={"verify_exp": False},
        )
        assert claims["sub"] == "mailto:me@example.com"
        assert claims["exp"] - NOW.timestamp() <= timedelta(hours=24).total_seconds()

    def test_key_file_is_created_once_and_private(self, tmp_path: Path) -> None:
        path = tmp_path / "keys" / "vapid.key"

        first = load_or_create_vapid_key(path)
        second = load_or_create_vapid_key(path)

        assert first.public_key == second.public_key
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


class TestEndpoints:
    ALLOWED = ("fcm.googleapis.com", "push.apple.com")

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://fcm.googleapis.com/fcm/send/x",
            "https://web.push.apple.com/QGx",
            "HTTPS://FCM.googleapis.com/a",
        ],
    )
    def test_known_push_services_are_allowed(self, endpoint: str) -> None:
        assert endpoint_allowed(endpoint, self.ALLOWED)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://fcm.googleapis.com/fcm/send/x",  # not https
            "https://localhost/push",
            "https://169.254.169.254/latest/meta-data",
            "https://evilfcm.googleapis.com.attacker.net/x",
            "https://notpush.apple.com.evil/x",
            "https://user:pw@fcm.googleapis.com/x",
            "fcm.googleapis.com/x",
        ],
    )
    def test_anything_else_is_refused(self, endpoint: str) -> None:
        assert not endpoint_allowed(endpoint, self.ALLOWED)

    def test_ids_are_stable_per_endpoint(self) -> None:
        assert subscription_id("https://a/1") == subscription_id("https://a/1")
        assert subscription_id("https://a/1") != subscription_id("https://a/2")
        assert len(subscription_id("https://a/1")) == 64


class TestNotifier:
    async def test_every_browser_gets_the_alert_encrypted_for_it(self) -> None:
        phone, laptop = Browser(), Browser()
        service = FakePushService()
        notifier, repository = make_notifier(
            service, [phone.subscribe("ana"), laptop.subscribe("bo")]
        )

        await notifier.send(alert())

        assert len(service.requests) == 2
        for browser in (phone, laptop):
            url, headers, body = service.request_for(browser)
            payload = json.loads(browser.decrypt(body))
            assert payload == {
                "kind": "motion",
                "event_id": "evt-1",
                "device_id": "porch",
                "device_name": "Porch",
                "started_at": "2026-10-05T11:59:48+00:00",
                "duration_seconds": 12.0,
                "image": "/snapshot/evt-1",
            }
            assert headers["Content-Encoding"] == "aes128gcm"
            assert headers["TTL"] == "3600"
            assert headers["Urgency"] == "high"
            assert headers["Authorization"].startswith("vapid t=")
            assert url == browser.endpoint
        assert repository.used == {phone.id: NOW, laptop.id: NOW}

    async def test_nobody_subscribed_is_not_an_error(self) -> None:
        service = FakePushService()
        notifier, _ = make_notifier(service, [])

        await notifier.send(alert())

        assert service.requests == []

    @pytest.mark.parametrize("status", [404, 410])
    async def test_expired_subscriptions_are_removed(self, status: int) -> None:
        gone, kept = Browser(), Browser()
        service = FakePushService({gone.endpoint: status})
        notifier, repository = make_notifier(
            service, [gone.subscribe("ana"), kept.subscribe("ana")]
        )

        await notifier.send(alert())

        assert [s.id for s in repository.items] == [kept.id]

    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_a_struggling_push_service_means_try_again(self, status: int) -> None:
        busy, fine = Browser(), Browser()
        service = FakePushService({busy.endpoint: status})
        notifier, repository = make_notifier(service, [busy.subscribe("ana"), fine.subscribe("bo")])

        with pytest.raises(NotificationError, match="1 of 2") as raised:
            await notifier.send(alert())

        assert raised.value.retryable
        assert len(repository.items) == 2

    async def test_network_errors_mean_try_again(self) -> None:
        browser = Browser()

        async def unreachable(*_: object) -> PushResponse:
            raise OSError("connection refused")

        notifier, _ = make_notifier(unreachable, [browser.subscribe("ana")])

        with pytest.raises(NotificationError) as raised:
            await notifier.send(alert())

        assert raised.value.retryable

    async def test_rejected_everywhere_is_given_up(self) -> None:
        browser = Browser()
        service = FakePushService({browser.endpoint: 403})
        notifier, repository = make_notifier(service, [browser.subscribe("ana")])

        with pytest.raises(NotificationError) as raised:
            await notifier.send(alert())

        assert not raised.value.retryable
        assert len(repository.items) == 1  # maybe our misconfiguration: the browser stays

    async def test_one_rejection_does_not_fail_the_others(self) -> None:
        rejected, fine = Browser(), Browser()
        service = FakePushService({rejected.endpoint: 400})
        notifier, _ = make_notifier(service, [rejected.subscribe("ana"), fine.subscribe("bo")])

        await notifier.send(alert())

    async def test_a_test_reports_failures_apart_from_expired_browsers(self) -> None:
        down, refused, gone = Browser(), Browser(), Browser()
        service = FakePushService({down.endpoint: 503, refused.endpoint: 400, gone.endpoint: 410})
        subscriptions = [b.subscribe("ana") for b in (down, refused, gone)]
        notifier, _ = make_notifier(service, subscriptions)

        result = await notifier.send_test("ana")

        assert (result.delivered, result.failed) == (0, 2)

    async def test_unusable_stored_keys_are_removed(self) -> None:
        broken = Browser().subscribe("ana", p256dh=b64url_encode(b"\x04" + b"\x00" * 64))
        service = FakePushService()
        notifier, repository = make_notifier(service, [broken])

        await notifier.send(alert())

        assert service.requests == []
        assert repository.items == []

    async def test_a_test_goes_only_to_your_own_browsers(self) -> None:
        mine, theirs = Browser(), Browser()
        service = FakePushService()
        notifier, _ = make_notifier(service, [mine.subscribe("ana"), theirs.subscribe("bo")])

        result = await notifier.send_test("ana")

        assert (result.delivered, result.failed) == (1, 0)
        _, headers, body = service.request_for(mine)
        assert json.loads(mine.decrypt(body)) == {"kind": "test"}
        assert headers["Urgency"] == "normal"


# ── Helpers ──────────────────────────────────────────────────


class MemorySubscriptions:
    def __init__(self, items: Sequence[PushSubscription]) -> None:
        self.items = list(items)
        self.used: dict[str, datetime] = {}

    async def save(self, subscription: PushSubscription) -> PushSubscription:
        self.items = [s for s in self.items if s.id != subscription.id] + [subscription]
        return subscription

    async def list(self, *, username: str | None = None) -> list[PushSubscription]:
        return [s for s in self.items if username is None or s.username == username]

    async def delete(self, subscription_id: str, *, username: str | None = None) -> bool:
        before = len(self.items)
        self.items = [
            s
            for s in self.items
            if not (s.id == subscription_id and username in {None, s.username})
        ]
        return len(self.items) < before

    async def mark_used(self, subscription_id: str, *, at: datetime) -> None:
        self.used[subscription_id] = at


def make_notifier(
    transport: object, subscriptions: Sequence[PushSubscription]
) -> tuple[WebPushNotifier, MemorySubscriptions]:
    repository = MemorySubscriptions(subscriptions)
    notifier = WebPushNotifier(
        repository,
        VapidKey.generate(),
        subject="mailto:me@example.com",
        ttl_seconds=3600,
        timeout_seconds=5,
        snapshot_link=lambda event_id: f"/snapshot/{event_id}",
        transport=transport,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return notifier, repository


def alert() -> Alert:
    started = NOW - timedelta(seconds=12)
    event = MotionEvent(
        id="evt-1",
        device_id="porch",
        started_at=started,
        ended_at=NOW,
        peak_area_ratio=0.2,
        motion_frames=40,
    )
    return Alert(device_id="porch", device_name="Porch", event=event, image_jpeg=b"jpeg")
