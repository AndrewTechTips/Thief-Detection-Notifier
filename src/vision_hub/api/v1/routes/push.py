"""Web push: browsers subscribe here to get alerts while the dashboard is closed."""

from typing import Annotated, Any

from fastapi import APIRouter, Path, status

from vision_hub.api.deps import CurrentPrincipal, PushServiceDep
from vision_hub.schemas.problem import ProblemDetail
from vision_hub.schemas.push import (
    PushConfigOut,
    PushSubscriptionIn,
    PushSubscriptionOut,
    PushTestOut,
)

type _Responses = dict[int | str, dict[str, Any]]


def _problems(*codes: int) -> _Responses:
    return {code: {"model": ProblemDetail} for code in codes}


router = APIRouter(prefix="/push", tags=["push"], responses=_problems(404))

SubscriptionId = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


@router.get("", summary="Get the push key")
async def get_push_config(push: PushServiceDep) -> PushConfigOut:
    """The hub's public VAPID key, to pass to `pushManager.subscribe()` as the
    `applicationServerKey`. 404 when push is turned off on the hub."""
    return PushConfigOut(public_key=push.public_key)


@router.post(
    "/subscriptions",
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe this browser",
    responses=_problems(400, 422),
)
async def subscribe(
    body: PushSubscriptionIn, push: PushServiceDep, principal: CurrentPrincipal
) -> PushSubscriptionOut:
    """Send `PushSubscription.toJSON()`. Subscribing the same browser again replaces its
    entry, under the account signed in now. Endpoints must be on a known push service."""
    subscription = await push.subscribe(
        principal, endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth
    )
    return PushSubscriptionOut.model_validate(subscription)


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unsubscribe a browser",
)
async def unsubscribe(
    subscription_id: SubscriptionId, push: PushServiceDep, principal: CurrentPrincipal
) -> None:
    """The id is the SHA-256 of the endpoint (hex). Only your own subscriptions."""
    await push.unsubscribe(principal, subscription_id)


@router.post("/test", summary="Send a test notification")
async def send_test(push: PushServiceDep, principal: CurrentPrincipal) -> PushTestOut:
    """To every browser subscribed under your account."""
    return PushTestOut(delivered=await push.send_test(principal))
