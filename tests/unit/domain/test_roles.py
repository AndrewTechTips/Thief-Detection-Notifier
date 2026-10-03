import pytest

from vision_hub.domain.auth import Role


@pytest.mark.parametrize(
    ("role", "required", "allowed"),
    [
        (Role.ADMIN, Role.ADMIN, True),
        (Role.ADMIN, Role.VIEWER, True),
        (Role.VIEWER, Role.VIEWER, True),
        (Role.VIEWER, Role.ADMIN, False),
    ],
)
def test_higher_roles_include_lower_ones(role: Role, required: Role, *, allowed: bool) -> None:
    assert role.includes(required) is allowed
