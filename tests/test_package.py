import importlib
from importlib.metadata import version

import pytest

import vision_hub

SUBPACKAGES = ["core", "api", "schemas", "domain", "services", "vision", "realtime", "infra"]


def test_version_matches_distribution_metadata() -> None:
    assert vision_hub.__version__ == version("vision-hub")


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_is_importable(name: str) -> None:
    module = importlib.import_module(f"vision_hub.{name}")
    assert module.__doc__
