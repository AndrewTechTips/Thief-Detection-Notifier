"""Cursor (keyset) pagination: stable under concurrent inserts, unlike offset pagination."""

import base64
import binascii
import json
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import Field

from vision_hub.core.errors import InvalidCursorError
from vision_hub.schemas.base import ApiSchema

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class Page[T](ApiSchema):
    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` to fetch the next page; null on the last page"
    )


class PageParams(ApiSchema):
    limit: int
    cursor: dict[str, Any] | None


def encode_cursor(position: dict[str, Any]) -> str:
    """Opaque to clients. Not signed: it only encodes a position, never authorisation."""
    raw = json.dumps(position, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        position = json.loads(raw)
    except binascii.Error, UnicodeDecodeError, ValueError:
        raise InvalidCursorError from None
    if not isinstance(position, dict):
        raise InvalidCursorError
    return position


def _page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> PageParams:
    return PageParams(limit=limit, cursor=decode_cursor(cursor) if cursor else None)


PageParamsDep = Annotated[PageParams, Depends(_page_params)]
