"""Web push alerts (RFC 8030): each browser gets the alert encrypted for it alone (RFC 8291,
``aes128gcm``), and the hub proves who sends it with its VAPID key (RFC 8292). Push services see
only ciphertext. Built on ``cryptography`` and PyJWT; the push services are plain HTTPS POSTs.

Subscriptions are URLs that browsers hand to the hub, so they are an SSRF risk: only HTTPS
endpoints on known push services are accepted (``domain.push.endpoint_allowed``), and redirects
are never followed.
"""

import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from vision_hub.core.logging import get_logger
from vision_hub.core.security import utc_now
from vision_hub.domain.notifications import Alert, NotificationError
from vision_hub.domain.push import PushSubscription, PushSubscriptionRepository, PushTestResult

logger = get_logger(__name__)

RECORD_SIZE = 4096
# Push services must accept bodies up to 4096 bytes (RFC 8030 §7.2); larger may be refused.
MAX_BODY = 4096
_HEADER = 16 + 4 + 1 + 65  # salt, record size, key id length, sender public key
_TAG_AND_DELIMITER = 16 + 1
VAPID_LIFETIME = timedelta(hours=12)  # RFC 8292 allows up to 24 h


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    """Strict base64url, with or without padding."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def public_key_from_bytes(data: bytes) -> ec.EllipticCurvePublicKey:
    """A browser's ``p256dh`` key; raises ValueError unless it is a valid P-256 point."""
    if len(data) != 65 or data[0] != 4:
        msg = "not an uncompressed P-256 public key"
        raise ValueError(msg)
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), data)


def encrypt(
    plaintext: bytes,
    *,
    receiver_key: bytes,
    auth_secret: bytes,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """The message body for one browser, as one ``aes128gcm`` record (RFC 8291 §3.4, RFC 8188).

    ``sender_key`` and ``salt`` are fresh for every message; they are parameters only so the
    RFC's worked example can be checked."""
    if len(plaintext) > MAX_BODY - _HEADER - _TAG_AND_DELIMITER:
        msg = f"payload too large: {len(plaintext)} bytes"
        raise ValueError(msg)
    receiver = public_key_from_bytes(receiver_key)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    sender_public = _public_bytes(sender_key.public_key())

    shared = sender_key.exchange(ec.ECDH(), receiver)
    key_info = b"WebPush: info\x00" + receiver_key + sender_public
    ikm = _hkdf(salt=auth_secret, ikm=shared, info=key_info, length=32)
    content_key = _hkdf(salt=salt, ikm=ikm, info=b"Content-Encoding: aes128gcm\x00", length=16)
    nonce = _hkdf(salt=salt, ikm=ikm, info=b"Content-Encoding: nonce\x00", length=12)
    # 0x02 marks the last (and only) record; no padding.
    ciphertext = AESGCM(content_key).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4) + bytes([len(sender_public)]) + sender_public
    return header + ciphertext


def _hkdf(*, salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


class VapidKey:
    """The hub's push identity: an ECDSA P-256 key. Browsers subscribe with its public half,
    and push services accept only messages signed with the private half."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        self._key = private_key
        self.public_key = b64url_encode(_public_bytes(private_key.public_key()))

    @classmethod
    def generate(cls) -> VapidKey:
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @classmethod
    def from_text(cls, text: str) -> VapidKey:
        """From the base64url private scalar (the format web-push tools print)."""
        try:
            raw = b64url_decode(text.strip())
            if len(raw) != 32:
                raise ValueError
            return cls(ec.derive_private_key(int.from_bytes(raw), ec.SECP256R1()))
        except ValueError:
            msg = "a VAPID private key must be 32 bytes, base64url-encoded"
            raise ValueError(msg) from None

    def to_text(self) -> str:
        return b64url_encode(self._key.private_numbers().private_value.to_bytes(32))

    def authorization(self, endpoint: str, *, subject: str, now: datetime) -> str:
        """The ``Authorization`` header for one push service (RFC 8292 §3)."""
        parts = urlsplit(endpoint)
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int((now + VAPID_LIFETIME).timestamp()),
            "sub": subject,
        }
        token = jwt.encode(claims, self._key, algorithm="ES256")
        return f"vapid t={token}, k={self.public_key}"


def load_or_create_vapid_key(path: Path) -> VapidKey:
    """Read the key, or create it once with owner-only permissions."""
    try:
        return VapidKey.from_text(path.read_text())
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = VapidKey.generate()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(key.to_text() + "\n")
    logger.warning(
        "vapid_key_generated",
        path=str(path),
        hint="back this file up: browsers subscribed with this key stop receiving alerts "
        "if it changes (they subscribe again the next time the dashboard opens)",
    )
    return key


@dataclass(frozen=True, slots=True)
class PushResponse:
    status: int
    body: str = ""


type PushTransport = Callable[[str, Mapping[str, str], bytes, float], Awaitable[PushResponse]]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a redirect is answered as the error it is


_opener = urllib.request.build_opener(_NoRedirects)


async def urllib_transport(
    url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
) -> PushResponse:
    """POST with the standard library, in a worker thread: alerts are few, so a connection
    pool would buy nothing."""

    def post() -> PushResponse:
        # https only: subscriptions are checked with endpoint_allowed before they are stored.
        request = urllib.request.Request(url, body, dict(headers), method="POST")  # noqa: S310
        try:
            with _opener.open(request, timeout=timeout_seconds) as response:
                return PushResponse(response.status)
        except urllib.error.HTTPError as error:
            detail = error.read(500).decode(errors="replace")
            return PushResponse(error.code, detail)

    return await asyncio.to_thread(post)


class Outcome(StrEnum):
    SENT = "sent"
    GONE = "gone"  # unsubscribed or expired: the subscription is deleted
    REJECTED = "rejected"  # trying again won't help
    RETRY = "retry"


type SnapshotLink = Callable[[str], str | None]


class WebPushNotifier:
    """The ``push`` alert channel: every subscribed browser gets the alert.

    Deliveries are retried as a whole when any browser's push service fails transiently, so a
    browser may receive an alert twice; the notification's ``tag`` (the event id) makes the
    second one replace the first silently."""

    def __init__(
        self,
        subscriptions: PushSubscriptionRepository,
        key: VapidKey,
        *,
        subject: str,
        ttl_seconds: int,
        timeout_seconds: float,
        snapshot_link: SnapshotLink | None = None,
        transport: PushTransport = urllib_transport,
        clock: Callable[[], datetime] = utc_now,
        max_concurrent: int = 8,
    ) -> None:
        self._subscriptions = subscriptions
        self._key = key
        self._subject = subject
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._snapshot_link = snapshot_link
        self._transport = transport
        self._clock = clock
        self._slots = asyncio.Semaphore(max_concurrent)

    @property
    def name(self) -> str:
        return "push"

    @property
    def public_key(self) -> str:
        return self._key.public_key

    async def has_recipients(self) -> bool:
        return bool(await self._subscriptions.list())

    async def send(self, alert: Alert) -> None:
        subscriptions = await self._subscriptions.list()
        if not subscriptions:
            return
        outcomes = await self._fan_out(subscriptions, self.payload(alert), urgency="high")
        if Outcome.RETRY in outcomes:
            failed = outcomes.count(Outcome.RETRY)
            msg = f"{failed} of {len(outcomes)} push services did not accept the alert"
            raise NotificationError(msg, retryable=True)
        if Outcome.SENT not in outcomes and Outcome.REJECTED in outcomes:
            msg = "every push service rejected the alert"
            raise NotificationError(msg, retryable=False)

    async def send_test(self, username: str) -> PushTestResult:
        """A test notification to ``username``'s browsers. Subscriptions found expired are
        removed and counted in neither number."""
        subscriptions = await self._subscriptions.list(username=username)
        outcomes = await self._fan_out(subscriptions, {"kind": "test"}, urgency="normal")
        failed = outcomes.count(Outcome.RETRY) + outcomes.count(Outcome.REJECTED)
        return PushTestResult(delivered=outcomes.count(Outcome.SENT), failed=failed)

    def payload(self, alert: Alert) -> dict[str, Any]:
        """What the service worker needs to show the alert; it formats the text itself, in the
        browser's language and time zone."""
        event = alert.event
        duration = (event.ended_at - event.started_at).total_seconds() if event.ended_at else None
        return {
            "kind": "motion",
            "event_id": event.id,
            "device_id": alert.device_id,
            "device_name": alert.device_name,
            "started_at": event.started_at.isoformat(),
            "duration_seconds": duration,
            "image": self._snapshot_link(event.id) if self._snapshot_link else None,
        }

    async def _fan_out(
        self, subscriptions: Sequence[PushSubscription], payload: dict[str, Any], *, urgency: str
    ) -> list[Outcome]:
        plaintext = json.dumps(payload, separators=(",", ":")).encode()
        return list(
            await asyncio.gather(
                *(self._deliver(subscription, plaintext, urgency) for subscription in subscriptions)
            )
        )

    async def _deliver(
        self, subscription: PushSubscription, plaintext: bytes, urgency: str
    ) -> Outcome:
        log = logger.bind(subscription=subscription.id[:12], username=subscription.username)
        try:
            body = encrypt(
                plaintext,
                receiver_key=b64url_decode(subscription.p256dh),
                auth_secret=b64url_decode(subscription.auth),
            )
        except ValueError as exc:
            log.warning("push_subscription_unusable", error=str(exc))
            await self._subscriptions.delete(subscription.id)
            return Outcome.GONE
        headers = {
            "Authorization": self._key.authorization(
                subscription.endpoint, subject=self._subject, now=self._clock()
            ),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(self._ttl),
            "Urgency": urgency,
        }
        try:
            async with self._slots:
                response = await self._transport(
                    subscription.endpoint, headers, body, self._timeout
                )
        except (OSError, TimeoutError) as exc:
            log.warning("push_failed", error=str(exc))
            return Outcome.RETRY
        outcome = _classify(response.status)
        if outcome is Outcome.SENT:
            await self._subscriptions.mark_used(subscription.id, at=self._clock())
        elif outcome is Outcome.GONE:
            # The browser unsubscribed, or the push service expired it.
            await self._subscriptions.delete(subscription.id)
            log.info("push_subscription_expired", status=response.status)
        else:
            log.warning("push_failed", status=response.status, detail=response.body[:200])
        return outcome


def _classify(status: int) -> Outcome:
    if 200 <= status < 300:
        return Outcome.SENT
    if status in {404, 410}:
        return Outcome.GONE
    if status == 429 or status >= 500:
        return Outcome.RETRY
    return Outcome.REJECTED
