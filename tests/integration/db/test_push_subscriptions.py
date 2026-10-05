from datetime import UTC, datetime, timedelta

from vision_hub.domain.auth import Role
from vision_hub.domain.push import PushSubscription, subscription_id
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.repositories.push import SqlPushSubscriptionRepository
from vision_hub.infra.db.repositories.users import SqlUserRepository

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def subscription(endpoint: str, username: str = "ana", **fields: object) -> PushSubscription:
    values: dict[str, object] = {
        "id": subscription_id(endpoint),
        "username": username,
        "endpoint": endpoint,
        "p256dh": "BPublicKey",
        "auth": "secret",
        "created_at": NOW,
    }
    return PushSubscription(**(values | fields))  # type: ignore[arg-type]


async def users(sessions: Sessions, *names: str) -> None:
    repository = SqlUserRepository(sessions)
    for name in names:
        await repository.create_if_missing(name, "$argon2id$hash", Role.VIEWER)


async def test_save_list_and_delete(sessions: Sessions) -> None:
    await users(sessions, "ana", "bo")
    repository = SqlPushSubscriptionRepository(sessions)
    phone = await repository.save(subscription("https://push.example/phone"))
    laptop = await repository.save(subscription("https://push.example/laptop", "bo"))

    assert await repository.list() == [phone, laptop]
    assert await repository.list(username="bo") == [laptop]
    assert not await repository.delete(phone.id, username="bo")  # not bo's
    assert await repository.delete(phone.id, username="ana")
    assert not await repository.delete(phone.id)
    assert await repository.list() == [laptop]


async def test_subscribing_again_replaces_keys_and_owner_but_not_the_date(
    sessions: Sessions,
) -> None:
    await users(sessions, "ana", "bo")
    repository = SqlPushSubscriptionRepository(sessions)
    await repository.save(subscription("https://push.example/1"))

    again = await repository.save(
        subscription("https://push.example/1", "bo", auth="new", created_at=NOW + timedelta(days=1))
    )

    assert (again.username, again.auth, again.created_at) == ("bo", "new", NOW)
    assert await repository.list() == [again]


async def test_successful_deliveries_are_recorded(sessions: Sessions) -> None:
    await users(sessions, "ana")
    repository = SqlPushSubscriptionRepository(sessions)
    saved = await repository.save(subscription("https://push.example/1"))

    await repository.mark_used(saved.id, at=NOW + timedelta(minutes=5))

    [stored] = await repository.list()
    assert stored.last_success_at == NOW + timedelta(minutes=5)
