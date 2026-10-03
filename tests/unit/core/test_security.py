from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from vision_hub.core.config import SecurityConfig
from vision_hub.core.errors import AuthenticationError
from vision_hub.core.security import PasswordHasher, TokenService, TokenType
from vision_hub.domain.auth import Principal, Role

SECRET = "s" * 48
ADMIN = Principal(username="admin", role=Role.ADMIN)


def config(**overrides: object) -> SecurityConfig:
    return SecurityConfig(jwt_secret=SecretStr(SECRET), **overrides)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture(scope="module")
def stored_hash(hasher: PasswordHasher) -> str:
    return hasher.hash("hunter2-but-longer")


class TestPasswordHasher:
    def test_hash_is_argon2id_and_salted(self, hasher: PasswordHasher, stored_hash: str) -> None:
        assert stored_hash.startswith("$argon2id$")
        assert hasher.hash("hunter2-but-longer") != stored_hash

    async def test_correct_password_verifies(
        self, hasher: PasswordHasher, stored_hash: str
    ) -> None:
        assert await hasher.verify("hunter2-but-longer", stored_hash) is True

    async def test_wrong_password_fails(self, hasher: PasswordHasher, stored_hash: str) -> None:
        assert await hasher.verify("wrong", stored_hash) is False

    async def test_unknown_user_still_runs_a_verification(self, hasher: PasswordHasher) -> None:
        assert await hasher.verify("anything", None) is False
        assert "_dummy_hash" in vars(hasher)  # a real Argon2 verify ran against a dummy hash

    async def test_unrecognised_hash_format_fails_closed(self, hasher: PasswordHasher) -> None:
        assert await hasher.verify("x", "$md5$not-supported") is False


class TestTokenService:
    def test_round_trip_preserves_identity(self) -> None:
        service = TokenService(config())

        issued = service.issue(ADMIN, TokenType.ACCESS)
        claims = service.decode(issued.token, TokenType.ACCESS)

        assert claims.principal == ADMIN
        assert claims.token_id == issued.claims.token_id
        assert issued.expires_in == 15 * 60

    def test_refresh_tokens_live_longer(self) -> None:
        issued = TokenService(config(refresh_token_ttl_days=7)).issue(ADMIN, TokenType.REFRESH)

        assert issued.expires_in == 7 * 24 * 3600

    def test_each_token_has_a_unique_id(self) -> None:
        service = TokenService(config())

        ids = {service.issue(ADMIN, TokenType.ACCESS).claims.token_id for _ in range(5)}

        assert len(ids) == 5

    def test_expired_token_is_rejected(self) -> None:
        an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        token = TokenService(config(), clock=lambda: an_hour_ago).issue(ADMIN, TokenType.ACCESS)

        with pytest.raises(AuthenticationError, match="expired"):
            TokenService(config()).decode(token.token, TokenType.ACCESS)

    @pytest.mark.parametrize(
        ("issued_as", "expected"),
        [(TokenType.REFRESH, TokenType.ACCESS), (TokenType.ACCESS, TokenType.REFRESH)],
    )
    def test_token_types_are_not_interchangeable(
        self, issued_as: TokenType, expected: TokenType
    ) -> None:
        service = TokenService(config())
        token = service.issue(ADMIN, issued_as).token

        with pytest.raises(AuthenticationError, match="invalid"):
            service.decode(token, expected)

    def test_token_signed_with_another_secret_is_rejected(self) -> None:
        forged = TokenService(SecurityConfig(jwt_secret=SecretStr("o" * 48))).issue(
            ADMIN, TokenType.ACCESS
        )

        with pytest.raises(AuthenticationError):
            TokenService(config()).decode(forged.token, TokenType.ACCESS)

    def test_unsigned_alg_none_token_is_rejected(self) -> None:
        valid = TokenService(config()).issue(ADMIN, TokenType.ACCESS)
        payload = jwt.decode(valid.token, options={"verify_signature": False})
        unsigned = jwt.encode(payload, key=None, algorithm="none")

        with pytest.raises(AuthenticationError):
            TokenService(config()).decode(unsigned, TokenType.ACCESS)

    def test_tampered_payload_is_rejected(self) -> None:
        token = (
            TokenService(config()).issue(Principal("viewer", Role.VIEWER), TokenType.ACCESS).token
        )
        header, _payload, signature = token.split(".")
        claims = jwt.decode(token, options={"verify_signature": False})
        claims["role"] = "admin"
        forged_payload = jwt.encode(claims, SECRET, algorithm="HS256").split(".")[1]

        with pytest.raises(AuthenticationError):
            TokenService(config()).decode(
                f"{header}.{forged_payload}.{signature}", TokenType.ACCESS
            )

    @pytest.mark.parametrize("field", ["jwt_audience", "jwt_issuer"])
    def test_audience_and_issuer_must_match(self, field: str) -> None:
        token = TokenService(config(**{field: "someone-else"})).issue(ADMIN, TokenType.ACCESS)

        with pytest.raises(AuthenticationError):
            TokenService(config()).decode(token.token, TokenType.ACCESS)

    @pytest.mark.parametrize("missing", ["jti", "role", "typ"])
    def test_required_claims_are_enforced(self, missing: str) -> None:
        token = TokenService(config()).issue(ADMIN, TokenType.ACCESS).token
        claims = jwt.decode(token, options={"verify_signature": False})
        del claims[missing]

        with pytest.raises(AuthenticationError):
            TokenService(config()).decode(
                jwt.encode(claims, SECRET, algorithm="HS256"), TokenType.ACCESS
            )

    def test_unknown_role_is_rejected(self) -> None:
        token = TokenService(config()).issue(ADMIN, TokenType.ACCESS).token
        claims = jwt.decode(token, options={"verify_signature": False})
        claims["role"] = "superuser"

        with pytest.raises(AuthenticationError):
            TokenService(config()).decode(
                jwt.encode(claims, SECRET, algorithm="HS256"), TokenType.ACCESS
            )

    def test_garbage_is_rejected(self) -> None:
        with pytest.raises(AuthenticationError, match="invalid"):
            TokenService(config()).decode("not-a-jwt", TokenType.ACCESS)
