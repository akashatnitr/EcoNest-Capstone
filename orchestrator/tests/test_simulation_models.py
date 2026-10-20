"""Tests for the first, data-only synthetic household simulation milestone."""

import pytest
from pydantic import ValidationError

from orchestrator.simulation.models import SimulatedDevice, SimulatedHouseholdTemplate
from orchestrator.simulation.templates import starter_households


def test_starter_households_are_isolated_and_valid() -> None:
    """Starter templates provide distinct houses without live integrations."""
    first = starter_households()
    second = starter_households()

    assert {household.id for household in first} == {"city_apartment", "family_home"}
    assert first[0] is not second[0]
    assert all(household.schema_version == 1 for household in first)
    assert all(household.devices for household in first)


def test_simulated_device_domain_must_match_its_entity_id() -> None:
    """Templates cannot create ambiguous Home Assistant-shaped entities."""
    with pytest.raises(ValidationError, match="entity_id domain must match domain"):
        SimulatedDevice(
            entity_id="light.kitchen",
            name="Kitchen Light",
            domain="switch",
            state="off",
        )


def test_scenario_references_stay_inside_its_household() -> None:
    """A scenario cannot accidentally modify another simulated household."""
    payload = starter_households()[0].model_dump()
    payload["scenarios"][0]["events"][0]["entity_id"] = "light.other_house"
    payload["scenarios"][0]["events"][0]["event_type"] = "state_change"
    payload["scenarios"][0]["events"][0]["state"] = "on"

    with pytest.raises(ValidationError, match="unknown entities"):
        SimulatedHouseholdTemplate.model_validate(payload)
