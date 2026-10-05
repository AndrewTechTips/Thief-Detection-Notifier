"""A browser's side of web push (RFC 8291) and a push service, for tests."""

from collections.abc import Mapping
from datetime import UTC, datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from vision_hub.domain.push import PushSubscription, subscription_id
from vision_hub.infra.notifiers.webpush import PushResponse, b64url_encode

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class Browser:
    """The receiving side of RFC 8291, as a browser implements it."""

    count = 0

    def __init__(self) -> None:
        Browser.count += 1
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.public = self._key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.auth = bytes(range(16))
        self.endpoint = f"https://fcm.googleapis.com/fcm/send/browser-{Browser.count}"
        self.id = subscription_id(self.endpoint)

    def subscribe(self, username: str, *, p256dh: str | None = None) -> PushSubscription:
        return PushSubscription(
            id=self.id,
            username=username,
            endpoint=self.endpoint,
            p256dh=p256dh or b64url_encode(self.public),
            auth=b64url_encode(self.auth),
            created_at=NOW,
        )

    def decrypt(self, body: bytes) -> bytes:
        salt, record_size, id_length = body[:16], int.from_bytes(body[16:20]), body[20]
        sender_public = body[21 : 21 + id_length]
        ciphertext = body[21 + id_length :]
        assert record_size == 4096
        sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public)
        shared = self._key.exchange(ec.ECDH(), sender)
        ikm = hkdf(self.auth, shared, b"WebPush: info\x00" + self.public + sender_public, 32)
        key = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        assert plaintext.endswith(b"\x02")
        return plaintext[:-1]


def hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


class FakePushService:
    def __init__(self, statuses: Mapping[str, int] | None = None) -> None:
        self.statuses = dict(statuses or {})
        self.requests: list[tuple[str, dict[str, str], bytes]] = []

    async def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
    ) -> PushResponse:
        self.requests.append((url, dict(headers), body))
        return PushResponse(self.statuses.get(url, 201))

    def request_for(self, browser: Browser) -> tuple[str, dict[str, str], bytes]:
        return next(r for r in self.requests if r[0] == browser.endpoint)
