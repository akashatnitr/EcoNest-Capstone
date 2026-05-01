import requests
import json

# =============================================================================
# Home Context: Scenario 1 — Oven on at 2am, home unoccupied
# =============================================================================
alert_context = {
    "home_identity": {
        "home_id": 1,
        "name": "EcoNest Unit 1"
    },
    "device_inventory": [
        {"room": "Kitchen", "device": "Dishwasher", "active_hours": [{"start": 21, "end": 23}], "active_days": "daily"},
        {"room": "Kitchen", "device": "Microwave", "active_hours": [{"start": 7, "end": 9}, {"start": 16, "end": 19}], "active_days": "daily"},
        {"room": "Kitchen", "device": "Oven", "active_hours": [], "active_days": "weekends"},
        {"room": "Master Bedroom", "device": "Master Bedroom TV", "active_hours": [{"start": 19, "end": 22}], "active_days": "daily"},
        {"room": "Living Room", "device": "Xbox", "active_hours": [{"start": 16, "end": 22}], "active_days": "weekdays"},
        {"room": "Living Room", "device": "Living Room TV", "active_hours": [{"start": 7, "end": 9}, {"start": 16, "end": 18}], "active_days": "daily"},
        {"room": "Computer Room", "device": "Computer", "active_hours": [{"start": 9, "end": 18}], "active_days": "daily"},
        {"room": "Laundry", "device": "Washing Machine", "active_hours": [], "active_days": "weekends"},
        {"room": "Laundry", "device": "Dryer", "active_hours": [], "active_days": "weekends"}
    ],
    "short_term_state": {
        "current_time": "02:15",
        "current_day": "Wednesday",
        "occupancy_estimate": "unoccupied",
        "motion_detected": False,
        "sound_spike": False,
        "active_devices": [
            {"room": "Kitchen", "device": "Oven", "current_watts": 2400}
        ],
        "total_active_watts": 2400,
        "power_trend": "sustained_high"
    },
    "long_term_behavior": {
        "avg_power_midnight_to_5am": 45,
        "motion_probability_2am": 0.02,
        "oven_typical_usage": "weekends only, never past 11pm",
        "weekly_avg_watts": 310
    },
    "system_goals": {
        "energy_weight": 0.7,
        "security_weight": 0.3
    }
}

# =============================================================================
# Home Context: Scenario 2 — Normal evening, high weekly energy usage
# =============================================================================
recommendation_context = {
    "home_identity": {
        "home_id": 1,
        "name": "EcoNest Unit 1"
    },
    "device_inventory": [
        {"room": "Kitchen", "device": "Dishwasher", "active_hours": [{"start": 21, "end": 23}], "active_days": "daily"},
        {"room": "Kitchen", "device": "Microwave", "active_hours": [{"start": 7, "end": 9}, {"start": 16, "end": 19}], "active_days": "daily"},
        {"room": "Kitchen", "device": "Oven", "active_hours": [], "active_days": "weekends"},
        {"room": "Master Bedroom", "device": "Master Bedroom TV", "active_hours": [{"start": 19, "end": 22}], "active_days": "daily"},
        {"room": "Living Room", "device": "Xbox", "active_hours": [{"start": 16, "end": 22}], "active_days": "weekdays"},
        {"room": "Living Room", "device": "Living Room TV", "active_hours": [{"start": 7, "end": 9}, {"start": 16, "end": 18}], "active_days": "daily"},
        {"room": "Computer Room", "device": "Computer", "active_hours": [{"start": 9, "end": 18}], "active_days": "daily"},
        {"room": "Laundry", "device": "Washing Machine", "active_hours": [], "active_days": "weekends"},
        {"room": "Laundry", "device": "Dryer", "active_hours": [], "active_days": "weekends"}
    ],
    "short_term_state": {
        "current_time": "21:30",
        "current_day": "Monday",
        "occupancy_estimate": "occupied",
        "motion_detected": True,
        "sound_spike": False,
        "active_devices": [
            {"room": "Kitchen", "device": "Dishwasher", "current_watts": 1200}
        ],
        "total_active_watts": 1200,
        "power_trend": "normal"
    },
    "long_term_behavior": {
        "avg_power_weekday": 310,
        "avg_power_weekend": 890,
        "weekly_kwh": 74,
        "weekly_kwh_previous": 51,
        "dryer_avg_weekly_runs": 1,
        "dryer_runs_this_week": 3,
        "washer_avg_weekly_runs": 1,
        "washer_runs_this_week": 3,
        "dishwasher_avg_daily_runs": 1,
        "dishwasher_runs_today": 3
    },
    "system_goals": {
        "energy_weight": 0.7,
        "security_weight": 0.3
    }
}

# =============================================================================
# Anomaly Detection
# =============================================================================
def detect_anomaly(short_term_state, device_inventory):
    if short_term_state["power_trend"] in ["spiking", "sustained_high"]:
        return True

    if short_term_state["sound_spike"]:
        return True

    current_hour = int(short_term_state["current_time"].split(":")[0])
    current_day = short_term_state["current_day"]
    is_weekend = current_day in ["Saturday", "Sunday"]

    for active_device in short_term_state["active_devices"]:
        for profile in device_inventory:
            if profile["device"] == active_device["device"]:
                if profile["active_days"] == "weekends" and not is_weekend:
                    return True
                if profile["active_days"] == "weekdays" and is_weekend:
                    return True
                hours = profile["active_hours"]
                if hours:
                    in_window = any(h["start"] <= current_hour <= h["end"] for h in hours)
                    if not in_window:
                        return True

    return False

# =============================================================================
# Inference
# =============================================================================
def run_inference(context, mode):
    if mode == "alert":
        prompt = f"""
You are a smart home reasoning agent monitoring for safety and energy anomalies.
An unusual event has been detected. Analyze the home context and return a JSON object with exactly these keys:
- alert: A single 2-3 sentence paragraph in plain language that explains what is wrong, why it is a problem, and what the homeowner should do immediately. Write it as if talking directly to the homeowner.
- recommendation: null

Home Context:
{json.dumps(context, indent=2)}

Respond ONLY with valid JSON. No extra text.
"""
    else:
        prompt = f"""
You are a smart home reasoning agent performing a routine energy review.
Analyze the home context and return a JSON object with exactly these keys:
- alert: null
- recommendation: A single 2-3 sentence paragraph in plain language that explains what the issue is, why it matters, and what the homeowner should do about it. Write it as if talking directly to the homeowner.

Home Context:
{json.dumps(context, indent=2)}

Respond ONLY with valid JSON. No extra text.
"""

    response = requests.post("http://localhost:11434/api/generate", json={
        "model": "mistral",
        "prompt": prompt,
        "stream": False
    })

    raw = response.json()["response"].strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)

# =============================================================================
# Run Scenarios
# =============================================================================
print("\nSCENARIO 1: Oven on at 2am, home unoccupied")
print("\n")
mode1 = "alert" if detect_anomaly(alert_context["short_term_state"], alert_context["device_inventory"]) else "routine"
print(f"Detected mode: {mode1.upper()}")
result1 = run_inference(alert_context, mode1)
print(f"\nAlert: {result1['alert']}")

print("\n")
print("SCENARIO 2: Normal evening, high weekly energy usage")
print("\n")
mode2 = "alert" if detect_anomaly(recommendation_context["short_term_state"], recommendation_context["device_inventory"]) else "routine"
print(f"Detected mode: {mode2.upper()}")
result2 = run_inference(recommendation_context, mode2)
print(f"\nRecommendation: {result2['recommendation']}")