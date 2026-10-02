"""Tests for conservative household behavior-profile calculations."""

from orchestrator.core.behavior_profile import (
    _appliance_profile,
    _comfort_profile,
    _motion_profile,
)


def test_comfort_profile_only_calls_a_target_a_preference_with_enough_stable_evidence() -> None:
    stable = _comfort_profile(
        {
            "room": "Media Room",
            "observations": 12,
            "observed_days": 12,
            "min_target_f": 78,
            "max_target_f": 80,
        }
    )
    sparse = _comfort_profile(
        {
            "room": "Guest Room",
            "observations": 2,
            "observed_days": 1,
            "min_target_f": 72,
            "max_target_f": 72,
        }
    )

    assert stable["interpretation"] == "consistent observed thermostat target — confirmation needed"
    assert stable["confidence"] == "medium"
    assert sparse["interpretation"] == "observed thermostat target — confirmation needed"
    assert sparse["confidence"] == "low"


def test_appliance_profile_keeps_its_timing_evidence_separate() -> None:
    profile = _appliance_profile(
        {"entity_id": "sensor.dryer_job_state", "completed_cycles": 7},
        {"weekday": "Tuesday", "hour_of_day": 16, "occurrences": 3},
    )

    assert profile["confidence"] == "low"
    assert profile["most_observed_time"] == {
        "weekday": "Tuesday",
        "hour_of_day": 16,
        "occurrences": 3,
    }


def test_motion_profile_does_not_claim_that_someone_is_home() -> None:
    profile = _motion_profile({"area": "Hall", "motion_events": 30})

    assert profile["confidence"] == "high"
    assert "not proof" in profile["note"]
