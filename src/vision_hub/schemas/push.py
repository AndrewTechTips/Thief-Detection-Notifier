"""Web push subscriptions, in the shape ``PushSubscription.toJSON()`` produces."""

import base64
from typing import Annotated

from pydantic import AfterValidator, Field

from vision_hub.schemas.base import ApiSchema, RequestSchema, UtcDateTime

_BASE64URL = r"^[A-Za-z0-9_-]+={0,2}$"


def _decoded_length(length: int, *, first: int | None = None) -> AfterValidator:
    def check(value: str) -> str:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(raw) != length or (first is not None and raw[0] != first):
            msg = f"must decode to {length} bytes"
            raise ValueError(msg)
        return value

    return AfterValidator(check)


class PushKeysIn(RequestSchema):
    # An uncompressed P-256 point (0x04, x, y) and a 16-byte secret (RFC 8291 §2-3).
    p256dh: Annotated[str, Field(max_length=100, pattern=_BASE64URL), _decoded_length(65, first=4)]
    auth: Annotated[str, Field(max_length=50, pattern=_BASE64URL), _decoded_length(16)]


class PushSubscriptionIn(RequestSchema):
    endpoint: Annotated[str, Field(min_length=12, max_length=1024)]
    keys: PushKeysIn
    expirationTime: float | None = None  # noqa: N815 - sent by browsers as is; ignored


class PushSubscriptionOut(ApiSchema):
    id: Annotated[str, Field(description="SHA-256 of the endpoint, hex")]
    created_at: UtcDateTime


class PushConfigOut(ApiSchema):
    public_key: Annotated[str, Field(description="`applicationServerKey` to subscribe with")]


class PushTestOut(ApiSchema):
    delivered: Annotated[int, Field(description="Browsers whose push service accepted it")]
