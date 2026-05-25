"""Tests for RBAC and ABAC permission helpers."""

from orchestrator.core.permissions import (
    AGENT_RUN,
    DEVICE_READ,
    DEVICE_WRITE,
    ROOM_READ,
    AccessContext,
    Role,
    can_access_device,
    can_access_room,
    can_perform,
    has_minimum_role,
    has_permission,
    is_within_allowed_hours,
    normalize_role,
    role_rank,
    to_frozenset,
)


def test_normalize_role_accepts_enum_and_string() -> None:
    assert normalize_role(Role.HOMEOWNER) == Role.HOMEOWNER
    assert normalize_role("homeowner") == Role.HOMEOWNER
    assert normalize_role("unknown") is None


def test_has_permission_accepts_string_roles() -> None:
    assert has_permission("homeowner", DEVICE_WRITE)
    assert has_permission("guest", DEVICE_READ)
    assert not has_permission("guest", DEVICE_WRITE)


def test_service_account_permissions_are_limited() -> None:
    assert has_permission("service_account", DEVICE_READ)
    assert has_permission("service_account", ROOM_READ)
    assert has_permission("service_account", AGENT_RUN)
    assert not has_permission("service_account", DEVICE_WRITE)


def test_role_rank_unknown_role_is_lowest() -> None:
    assert role_rank("unknown") == -1
    assert role_rank("guest") < role_rank("homeowner")


def test_has_minimum_role_rejects_unknown_roles() -> None:
    assert has_minimum_role("homeowner", "guest")
    assert not has_minimum_role("unknown", "guest")
    assert not has_minimum_role("homeowner", "unknown")


def test_time_window_same_day() -> None:
    assert is_within_allowed_hours(10, 9, 17)
    assert not is_within_allowed_hours(20, 9, 17)


def test_time_window_crosses_midnight() -> None:
    assert is_within_allowed_hours(23, 22, 6)
    assert is_within_allowed_hours(3, 22, 6)
    assert not is_within_allowed_hours(12, 22, 6)


def test_can_access_room_by_household_and_explicit_room() -> None:
    context = AccessContext(
        user_household_id=1,
        resource_household_id=1,
        room_id=10,
        allowed_room_ids=to_frozenset([10, 11]),
    )

    assert can_access_room("family_member", context)


def test_can_access_room_denies_wrong_household() -> None:
    context = AccessContext(user_household_id=1, resource_household_id=2, room_id=10)

    assert not can_access_room("family_member", context)


def test_can_access_device_by_explicit_device() -> None:
    context = AccessContext(
        user_household_id=1,
        resource_household_id=1,
        room_id=10,
        device_id=99,
        allowed_room_ids=to_frozenset([10]),
        allowed_device_ids=to_frozenset([99]),
    )

    assert can_access_device("homeowner", context)


def test_can_perform_applies_rbac_and_time_restriction() -> None:
    context = AccessContext(
        current_hour=21,
        allowed_start_hour=8,
        allowed_end_hour=20,
    )

    assert not can_perform("family_member", DEVICE_WRITE, context)
    assert can_perform("family_member", DEVICE_WRITE, AccessContext(current_hour=12))
