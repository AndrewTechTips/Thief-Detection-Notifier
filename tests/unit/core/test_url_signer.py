from pydantic import SecretStr

from vision_hub.core.security import UrlSigner

SECRET = SecretStr("s" * 48)
RESOURCE = "/api/v1/events/abc/snapshot?kind=thumbnail"


def signer(now: float = 1_000_000.0, ttl: int = 3600, secret: SecretStr = SECRET) -> UrlSigner:
    return UrlSigner(secret, ttl_seconds=ttl, clock=lambda: now)


def test_valid_signature_verifies() -> None:
    expires, signature = signer().sign(RESOURCE)

    assert expires == 1_000_000 + 3600
    assert signer().verify(RESOURCE, expires, signature) is True
    assert "=" not in signature  # URL-safe without padding


def test_expired_links_are_refused() -> None:
    expires, signature = signer().sign(RESOURCE)

    assert signer(now=expires + 1).verify(RESOURCE, expires, signature) is False


def test_signature_is_bound_to_the_resource_and_expiry() -> None:
    expires, signature = signer().sign(RESOURCE)

    assert signer().verify(RESOURCE.replace("thumbnail", "clean"), expires, signature) is False
    assert signer().verify(RESOURCE, expires + 3600, signature) is False  # extended lifetime
    assert signer().verify(RESOURCE, expires, signature[:-1] + "A") is False


def test_other_secrets_cannot_forge_links() -> None:
    expires, signature = signer(secret=SecretStr("o" * 48)).sign(RESOURCE)

    assert signer().verify(RESOURCE, expires, signature) is False
