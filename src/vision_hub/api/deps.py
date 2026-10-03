"""Reusable ``Annotated`` dependencies: routes declare what they need by type annotation
(e.g. ``settings: SettingsDep``) instead of reaching for globals."""

from typing import Annotated, cast

from fastapi import Depends
from starlette.requests import HTTPConnection

from vision_hub.core.config import Settings
from vision_hub.core.container import Container


def get_container(connection: HTTPConnection) -> Container:
    """Works for both HTTP requests and WebSockets (both are ``HTTPConnection``)."""
    try:
        container = connection.state.container
    except AttributeError:
        msg = "Service container unavailable: the application lifespan has not started"
        raise RuntimeError(msg) from None
    return cast("Container", container)


ContainerDep = Annotated[Container, Depends(get_container)]


def get_app_settings(container: ContainerDep) -> Settings:
    return container.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
