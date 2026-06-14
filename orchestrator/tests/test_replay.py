"""Tests for replaying stored Home Assistant snapshots."""

from orchestrator.replay.harness import ReplayScenario, replay_feedback_scenarios


def test_replay_feedback_scenario_matches_expected_category():
    scenario = ReplayScenario(
        name="away-house-light-on",
        states=[
            {
                "entity_id": "person.econest",
                "state": "not_home",
                "attributes": {"friendly_name": "econest"},
            },
            {
                "entity_id": "light.media_room",
                "state": "on",
                "attributes": {"friendly_name": "Media Room Light"},
            },
            {
                "entity_id": "sensor.breaker_1_power_minute_average",
                "state": "1200",
                "attributes": {"friendly_name": "Input Breaker Power Minute Average"},
            },
        ],
        expected_categories={"energy"},
        expected_titles={"Lights are on while the house appears away"},
    )

    results = replay_feedback_scenarios([scenario])

    assert len(results) == 1
    assert results[0].passed
    assert results[0].snapshot["occupancy_status"] == "away"
