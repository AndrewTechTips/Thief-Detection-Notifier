"""The Docker image downloads the person model itself: it must be the model the hub expects."""

import re
from pathlib import Path

from vision_hub.vision.persons import MODEL_SHA256, MODEL_URL

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def arg(name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE.read_text(), re.MULTILINE)
    assert match, f"ARG {name} missing from the Dockerfile"
    return match.group(1)


def test_the_image_fetches_the_pinned_person_model() -> None:
    assert arg("PERSON_MODEL_URL") == MODEL_URL
    assert arg("PERSON_MODEL_SHA256") == MODEL_SHA256
