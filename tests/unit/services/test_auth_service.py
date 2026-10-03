import pytest
from pydantic import SecretStr

from vision_hub.core.config import SecurityConfig
from vision_hub.core.errors import AuthenticationError
from vision_hub.core.security import PasswordHasher, TokenService, TokenType
from vision_hub.domain.auth import Principal, Role, User
from vision_hub.infra.auth import InMemoryTokenRevocationStore
from vision_hub.services.auth import INVALID_CREDENTIALS, AuthService

PASSWORD = "a-long-admin-password"


class FakeUsers:
    def __init__(self, *users: User) -> None:
        self.users = {user.username: user for user in users}

    async def get_by_username(self, username: str) -> User | None:
        return self.users.get(username)


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture(scope="module")
def admin(hasher: PasswordHasher) -> User:
    return User(username="admin", role=Role.ADMIN, password_hash=hasher.hash(PASSWORD))


@pytest.fixture
def users(admin: User) -> FakeUsers:
    return FakeUsers(admin)


@pytest.fixture
def tokens() -> TokenService:
    return TokenService(SecurityConfig(jwt_secret=SecretStr("k" * 48)))


@pytest.fixture
def revocations() -> InMemoryTokenRevocationStore:
    return InMemoryTokenRevocationStore()


@pytest.fixture
def service(
    users: FakeUsers,
    hasher: PasswordHasher,
    tokens: TokenService,
    revocations: InMemoryTokenRevocationStore,
) -> AuthService:
    return AuthService(users=users, hasher=hasher, tokens=tokens, revocations=revocations)


class TestLogin:
    async def test_valid_credentials_issue_a_token_pair(
        self, service: AuthService, tokens: TokenService
    ) -> None:
        pair = await service.login("admin", PASSWORD)

        assert tokens.decode(pair.access.token, TokenType.ACCESS).principal == Principal(
            "admin", Role.ADMIN
        )
        assert tokens.decode(pair.refresh.token, TokenType.REFRESH).principal.username == "admin"

    @pytest.mark.parametrize(("username", "password"), [("admin", "wrong"), ("ghost", PASSWORD)])
    async def test_failures_share_one_message(
        self, service: AuthService, username: str, password: str
    ) -> None:
        with pytest.raises(AuthenticationError) as exc_info:
            await service.login(username, password)

        assert exc_info.value.detail == INVALID_CREDENTIALS


class TestRefresh:
    async def test_rotates_the_refresh_token(self, service: AuthService) -> None:
        first = await service.login("admin", PASSWORD)

        second = await service.refresh(first.refresh.token)

        assert second.refresh.token != first.refresh.token
        assert service.authenticate(second.access.token).username == "admin"

    async def test_reusing_a_refresh_token_is_rejected(self, service: AuthService) -> None:
        pair = await service.login("admin", PASSWORD)
        await service.refresh(pair.refresh.token)

        with pytest.raises(AuthenticationError, match="revoked"):
            await service.refresh(pair.refresh.token)

    async def test_picks_up_role_changes(self, service: AuthService, users: FakeUsers) -> None:
        pair = await service.login("admin", PASSWORD)
        users.users["admin"] = User("admin", Role.VIEWER, users.users["admin"].password_hash)

        refreshed = await service.refresh(pair.refresh.token)

        assert service.authenticate(refreshed.access.token).role is Role.VIEWER

    async def test_fails_for_removed_users(self, service: AuthService, users: FakeUsers) -> None:
        pair = await service.login("admin", PASSWORD)
        users.users.clear()

        with pytest.raises(AuthenticationError):
            await service.refresh(pair.refresh.token)

    async def test_access_token_cannot_refresh(self, service: AuthService) -> None:
        pair = await service.login("admin", PASSWORD)

        with pytest.raises(AuthenticationError):
            await service.refresh(pair.access.token)


class TestLogout:
    async def test_revokes_the_refresh_token(self, service: AuthService) -> None:
        pair = await service.login("admin", PASSWORD)

        await service.logout(pair.refresh.token)

        with pytest.raises(AuthenticationError, match="revoked"):
            await service.refresh(pair.refresh.token)


class TestAuthenticate:
    async def test_rejects_refresh_tokens(self, service: AuthService) -> None:
        pair = await service.login("admin", PASSWORD)

        with pytest.raises(AuthenticationError):
            service.authenticate(pair.refresh.token)
