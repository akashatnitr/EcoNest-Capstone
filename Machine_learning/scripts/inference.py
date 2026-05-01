import mysql.connector
import requests
import json
import argparse
import re
import os
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/Desktop/Econest/.env"))

# ----------------------------------------------------------------
# Config
# ----------------------------------------------------------------
PAUSED = True  # set to False to re-enable

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral"
LOG_FILE = os.path.expanduser("~/inference.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(message)s"
)

def log(msg):
    logging.info(msg)

def get_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="econest", 
        database="econest",
        port=3306,
        ssl_disabled=True
    )

# ----------------------------------------------------------------
# SMS via Gmail → carrier email gateway
# ----------------------------------------------------------------
def send_sms(message):
    if PAUSED:
        log("SMS suppressed: script is paused")
        return

    import smtplib
    from email.mime.text import MIMEText

    gmail    = os.environ.get("SMS_GMAIL")
    password = os.environ.get("SMS_GMAIL_APP_PASSWORD")
    to       = os.environ.get("SMS_TO")

    if not all([gmail, password, to]):
        log("SMS skipped: SMS credentials missing from .env")
        return

    try:
        msg = MIMEText(message)
        msg["From"]    = gmail
        msg["To"]      = to
        msg["Subject"] = ""

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail, password)
            server.sendmail(gmail, to, msg.as_string())

        log(f"SMS sent → {to}")
    except Exception as e:
        log(f"SMS failed: {e}")

# ----------------------------------------------------------------
# Laundry done detection via Home Assistant
HA_LAUNDRY_STATE_FILE = os.path.expanduser("~/.laundry_state.json")
HA_LAUNDRY_ENTITIES = {
    "washer": "sensor.washer_machine_state",
    "dryer":  "sensor.dryer_machine_state",
}
# States that mean the appliance is actively running a cycle
HA_RUNNING_STATES = {"run", "pause", "running", "wash", "rinse", "spin", "drying", "cooling"}
# States that mean the cycle just finished (trigger notification)
HA_DONE_STATES = {"stop", "idle", "finish", "end", "wrinkle_prevent"}

# ----------------------------------------------------------------
# Cheap hours — Texas TOU pricing tiers
# ----------------------------------------------------------------
def get_cheap_hours():
    hour = datetime.now().hour
    if hour >= 21 or hour < 6:
        tier = "off_peak"
        cents = 8
        next_cheap = "right now"
    elif 14 <= hour < 20:
        tier = "peak"
        cents = 18
        next_cheap = "after 9pm tonight"
    else:
        tier = "mid_peak"
        cents = 12
        next_cheap = "after 9pm tonight"

    return {
        "current_tier": tier,
        "cents_per_kwh": cents,
        "next_cheap_window": next_cheap,
        "off_peak_hours": "9pm–6am",
        "peak_hours": "2pm–8pm"
    }

def get_ha_entity_state(entity_id):
    ha_url   = os.environ.get("HA_URL", "http://localhost:8123")
    ha_token = os.environ.get("HA_TOKEN")
    if not ha_token:
        log("HA_TOKEN missing from .env — skipping laundry check")
        return None
    try:
        resp = requests.get(
            f"{ha_url}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {ha_token}"},
            timeout=5
        )
        if resp.status_code == 200:
            return resp.json().get("state", "").lower()
        log(f"HA API error {resp.status_code} for {entity_id}")
    except Exception as e:
        log(f"HA API request failed: {e}")
    return None


def _load_laundry_state():
    if os.path.exists(HA_LAUNDRY_STATE_FILE):
        try:
            with open(HA_LAUNDRY_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_laundry_state(state):
    try:
        with open(HA_LAUNDRY_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log(f"Failed to save laundry state: {e}")


def check_laundry_done():
    prev    = _load_laundry_state()
    current = {}
    pricing = get_cheap_hours()

    for appliance, entity_id in HA_LAUNDRY_ENTITIES.items():
        state = get_ha_entity_state(entity_id)
        if state is None:
            continue

        current[appliance] = state
        prev_state = prev.get(appliance, "")
        log(f"Laundry check — {appliance}: prev={prev_state!r} → current={state!r}")

        was_running = prev_state in HA_RUNNING_STATES
        is_done     = state in HA_DONE_STATES

        if was_running and is_done:
            if appliance == "washer":
                dryer_state   = get_ha_entity_state(HA_LAUNDRY_ENTITIES["dryer"])
                dryer_running = dryer_state in HA_RUNNING_STATES

                if not dryer_running:
                    if pricing["current_tier"] == "peak":
                        msg = (
                            f"Washer done! It's peak pricing right now "
                            f"({pricing['cents_per_kwh']}¢/kWh). "
                            f"Hold off on the dryer until 9pm "
                            f"— saves ~$0.20 per load (~$6/month)."
                        )
                    elif pricing["current_tier"] == "mid_peak":
                        msg = (
                            f"Washer done! Dryer will cost ~$0.28 at current rates "
                            f"({pricing['cents_per_kwh']}¢/kWh). "
                            f"Running after 9pm saves ~$0.18 per load."
                        )
                    else:
                        msg = (
                            f"Washer done! Great time to run the dryer — "
                            f"you're in off-peak hours ({pricing['cents_per_kwh']}¢/kWh). "
                            f"Cheapest it'll get today."
                        )
                else:
                    msg = "Washer done! Dryer is already running."
            else:
                msg = "Dryer done! Laundry is ready to fold."

            log(f"Laundry: {msg}")
            print(f"\n[LAUNDRY] {msg}\n")

    if current:
        _save_laundry_state(current)
def check_security(dry_run=True):
    hour     = datetime.now().hour
    is_night = hour >= 23 or hour < 6

    garage1       = get_ha_entity_state("cover.garage12")
    garage2       = get_ha_entity_state("cover.garage_door_3")
    garage_open   = garage1 == "open" or garage2 == "open"
    motion_front  = get_ha_entity_state("binary_sensor.hobeian_zg_204zl")
    motion_garage = get_ha_entity_state("binary_sensor.motion_sensor_garage")
    person_home   = get_ha_entity_state("person.econest")
    occupied      = person_home == "home" or motion_front == "on" or motion_garage == "on"

    oven_watts  = float(get_ha_entity_state("sensor.breaker_2_power_minute_average") or 0)
    total_watts = float(get_ha_entity_state("sensor.balance_power_minute_average") or 0)

    conditions = []

    if garage_open and is_night:
        which = []
        if garage1 == "open": which.append("Garage Door 1")
        if garage2 == "open": which.append("Garage Door 2")
        conditions.append({
            "type":     "GARAGE_OPEN_NIGHT",
            "detail":   f"{', '.join(which)} open at {hour}:00 (night hours)",
            "severity": "HIGH"
        })

    if garage_open and not occupied:
        conditions.append({
            "type":     "GARAGE_OPEN_EMPTY",
            "detail":   "Garage open and home appears empty",
            "severity": "HIGH"
        })

    if motion_front == "on" and is_night:
        conditions.append({
            "type":     "NIGHT_MOTION_FRONT",
            "detail":   f"Motion at front door at {hour}:00",
            "severity": "HIGH"
        })

    if motion_garage == "on" and is_night:
        conditions.append({
            "type":     "NIGHT_MOTION_GARAGE",
            "detail":   f"Motion in garage at {hour}:00",
            "severity": "HIGH"
        })

    if oven_watts > 100 and not occupied:
        conditions.append({
            "type":     "OVEN_HOME_EMPTY",
            "detail":   f"Oven drawing {oven_watts:.0f}W while home appears empty",
            "severity": "HIGH"
        })

    if not occupied and total_watts > 500 and not garage_open:
        conditions.append({
            "type":     "POWER_WHILE_AWAY",
            "detail":   f"Home appears empty but drawing {total_watts:.0f}W",
            "severity": "MEDIUM"
        })

    if not conditions:
        log("Security check: no conditions detected")
        return

    severity = "HIGH" if any(c["severity"] == "HIGH" for c in conditions) else "MEDIUM"

    log(f"Security check: {len(conditions)} condition(s) — severity={severity} dry_run={dry_run}")
    for c in conditions:
        log(f"  [{c['severity']}] {c['type']}: {c['detail']}")

    if severity != "HIGH":
        log("Security check: MEDIUM severity — no SMS")
        return

    conditions_text = "\n".join(
        f"- [{c['severity']}] {c['type']}: {c['detail']}"
        for c in conditions
    )

    prompt = f"""You are a smart home security monitoring agent.

Security conditions detected at {hour}:00:
{conditions_text}

Context:
- Person home: {person_home}
- Front door motion: {motion_front}
- Garage motion: {motion_garage}
- Garage 1: {garage1} | Garage 2: {garage2}
- Night hours: {is_night}
- Total home power: {total_watts:.0f}W
- Oven: {oven_watts:.0f}W

Generate a concise security alert for the homeowner.
Cross-reference all conditions — garage open + home empty at night is more serious than either alone.

RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT:
ALERT_TYPE: [INTRUSION | ACCESS | ENERGY | SAFETY | COMBINED]
SEVERITY: HIGH
ALERT: [2-3 sentences — reference exact conditions and context]
REASONING: [1 sentence — cite the specific combination of factors]
SMS: YES
SMS_MESSAGE: [under 140 chars]
"""

    log("Calling Mistral for security alert...")
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "stream": False
    })

    raw    = response.json()["response"].strip()
    result = parse_response(raw)

    log(f"Security alert: {result.get('alert')}")

    if dry_run:
        log(f"[DRY RUN] Would send SMS: {result.get('sms_message')}")
        print(f"\n[SECURITY DRY RUN] {result.get('alert')}")
        print(f"[SECURITY DRY RUN] Would SMS: {result.get('sms_message')}\n")
    else:
        if result.get("sms_message"):
            send_sms(result["sms_message"])
            log(f"SMS sent: {result['sms_message']}")

def check_laundry_cheap_hours():
    pricing = get_cheap_hours()

    if pricing["current_tier"] == "off_peak":
        log("Laundry cheap hours: already off-peak, skipping")
        return

    washer_state = get_ha_entity_state("sensor.washer_machine_state")
    dryer_state  = get_ha_entity_state("sensor.dryer_machine_state")

    washer_running = washer_state in HA_RUNNING_STATES
    dryer_running  = dryer_state in HA_RUNNING_STATES

    if not washer_running and not dryer_running:
        log("Laundry cheap hours: no appliances running, skipping")
        return

    active = []
    if washer_running:
        active.append("Washer")
    if dryer_running:
        active.append("Dryer")

    prompt = f"""You are a smart home energy advisor.

The following laundry appliances are currently running during {pricing['current_tier']} electricity hours ({pricing['cents_per_kwh']}¢/kWh):
Appliances running: {', '.join(active)}

Key facts:
- Peak hours are {pricing['peak_hours']} at {pricing['cents_per_kwh']}¢/kWh
- Off-peak hours are {pricing['off_peak_hours']} at 8¢/kWh
- Dryer uses ~3.3 kWh per cycle
- Washer uses ~0.5 kWh per cycle
- Next cheap window: {pricing['next_cheap_window']}

Generate a short friendly 2-3 sentence recommendation for the homeowner.
Include the exact cost difference in dollars between running now vs off-peak.
Be specific about which appliance and when to run it instead.

RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT:
CATEGORY: EFFICIENCY
PRIORITY: [HIGH if peak, MEDIUM if mid_peak]
RECOMMENDATION: [2-3 sentences with exact dollar savings]
REASONING: [1 sentence citing exact cents/kWh and cycle cost]
SMS: NO
SMS_MESSAGE:
"""

    log("Calling Mistral for laundry cheap hours recommendation...")
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "stream": False
    })

    raw = response.json()["response"].strip()
    result = parse_response(raw)

    if result["recommendation"]:
        print(f"\n[LAUNDRY ENERGY] {result['recommendation']}")
        print(f"[REASONING] {result['reasoning']}\n")
        log(f"Laundry recommendation: {result['recommendation']}")

# ----------------------------------------------------------------
# Sprinkler — soil moisture + weather + cheap hours
# ----------------------------------------------------------------
def check_sprinkler(dry_run=True):
    hour = datetime.now().hour
    if hour != 7:
        log("Sprinkler: not 7am, skipping")
        return
    
    ha_url   = os.environ.get("HA_URL", "http://localhost:8123")
    ha_token = os.environ.get("HA_TOKEN")
    headers  = {"Authorization": f"Bearer {ha_token}",
                 "Content-Type": "application/json"}

    # Read soil moisture
    soil_state = get_ha_entity_state("sensor.wifi_soil_sensor_humidity")
    try:
        soil_moisture = float(soil_state)
    except (TypeError, ValueError):
        log("Sprinkler: could not read soil moisture, skipping")
        return

    # Read current weather condition
    weather_state = get_ha_entity_state("weather.forecast_home")

    # Pull hourly forecast to check for upcoming rain
    try:
        forecast_resp = requests.post(
            f"{ha_url}/api/services/weather/get_forecasts",
            headers=headers,
            json={"entity_id": "weather.forecast_home", "type": "hourly"},
            params={"return_response": "true"}
        )
        forecasts = forecast_resp.json().get(
            "weather.forecast_home", {}
        ).get("forecast", [])
    except Exception as e:
        log(f"Sprinkler: forecast fetch failed — {e}")
        forecasts = []

    # Check next 6 hours for rain
    rain_conditions = {"rainy", "pouring", "lightning", "lightning-rainy", "hail"}
    upcoming_rain   = any(
        f.get("condition", "") in rain_conditions or f.get("precipitation", 0) > 0.05
        for f in forecasts[:6]
    )
    upcoming_precip = sum(f.get("precipitation", 0) for f in forecasts[:6])

    # Get cheap hours context
    pricing = get_cheap_hours()

    print(f"\n[SPRINKLER] Current conditions:")
    print(f"  Soil moisture : {soil_moisture}%")
    print(f"  Weather now   : {weather_state}")
    print(f"  Rain next 6hr : {'yes' if upcoming_rain else 'no'} ({upcoming_precip:.2f} in expected)")
    print(f"  Pricing tier  : {pricing['current_tier']} ({pricing['cents_per_kwh']}¢/kWh)")

    # Decision logic
    should_skip   = soil_moisture > 60 or upcoming_rain
    good_time     = pricing["current_tier"] == "off_peak"
    needs_water   = soil_moisture < 40

    if should_skip:
        reason = []
        if soil_moisture > 60:
            reason.append(f"soil moisture at {soil_moisture}%")
        if upcoming_rain:
            reason.append(f"rain expected ({upcoming_precip:.2f} in next 6hr)")
        print(f"  Decision      : SKIP — {', '.join(reason)}")
    elif needs_water and not good_time:
        print(f"  Decision      : DELAY — needs water but peak pricing, wait until 9pm")
    elif needs_water and good_time:
        print(f"  Decision      : WATER — soil dry and off-peak pricing")
        if not dry_run:
            # Trigger all three zones
            for valve in ["valve.side_r_lawn", "valve.back_porch_lawn", "valve.back_lawn"]:
                requests.post(
                    f"{ha_url}/api/services/valve/open_valve",
                    headers=headers,
                    json={"entity_id": valve}
                )
            log("Sprinkler: zones triggered")
        else:
            print("  [DRY RUN] Would trigger valve.side_r_lawn, valve.back_porch_lawn, valve.back_lawn")
    else:
        print(f"  Decision      : MONITOR — soil at {soil_moisture}%, no action needed")

    # Build Mistral prompt
    prompt = f"""You are a smart home energy and garden advisor.

Current sprinkler situation:
- Soil moisture: {soil_moisture}%
- Current weather: {weather_state}
- Rain expected in next 6 hours: {'yes' if upcoming_rain else 'no'} ({upcoming_precip:.2f} inches)
- Electricity tier: {pricing['current_tier']} ({pricing['cents_per_kwh']}¢/kWh)
- Next cheap window: {pricing['next_cheap_window']}
- Irrigation pump uses ~500W

Decision made: {'SKIP watering' if should_skip else 'DELAY until off-peak' if needs_water and not good_time else 'WATER NOW' if needs_water and good_time else 'MONITOR'}

Generate a short friendly 2-3 sentence recommendation explaining the decision.
If skipping, mention how many gallons are saved (~15 gallons per zone).
If delaying, mention the cost difference between now and off-peak.
If watering, confirm it is the right time.

RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT:
CATEGORY: EFFICIENCY
PRIORITY: [HIGH if needs water, LOW if skipping]
RECOMMENDATION: [2-3 sentences — mention soil %, weather, and pricing]
REASONING: [1 sentence — cite the exact data points]
SMS: NO
SMS_MESSAGE:
"""

    log("Calling Mistral for sprinkler recommendation...")
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "stream": False
    })

    raw    = response.json()["response"].strip()
    result = parse_response(raw)

    if result["recommendation"]:
        msg = f"[EcoNest Sprinkler] {result['recommendation']}"
        print(f"\n[SPRINKLER] {result['recommendation']}")
        print(f"[REASONING] {result['reasoning']}\n")
        log(f"Sprinkler recommendation: {result['recommendation']}")
        send_sms(msg)
# ----------------------------------------------------------------
# Late night wind-down — TV off, lights dim, thermostat to sleep temp
# ----------------------------------------------------------------
def check_late_night_winddown(dry_run=True):
    hour = datetime.now().hour

    if not (hour >= 23 or hour < 2):
        log("Wind-down: not late night hours, skipping")
        return

    ha_url   = os.environ.get("HA_URL", "http://localhost:8123")
    ha_token = os.environ.get("HA_TOKEN")
    headers  = {"Authorization": f"Bearer {ha_token}"}

    tv_state    = get_ha_entity_state("media_player.samsung_q70_series_55")
    light_state = get_ha_entity_state("light.master_bedroom_light_1")
    therm_state = get_ha_entity_state("climate.master_bedroom")

    print(f"\n[WIND-DOWN] Current states:")
    print(f"  TV        : {tv_state}")
    print(f"  Light     : {light_state}")
    print(f"  Thermostat: {therm_state}")

    actions_taken = []

    if tv_state in ("on", "playing"):
        if dry_run:
            print("  [DRY RUN] Would turn off TV")
        else:
            requests.post(
                f"{ha_url}/api/services/media_player/turn_off",
                headers=headers,
                json={"entity_id": "media_player.samsung_q70_series_55"}
            )
        actions_taken.append("TV turned off")
        log(f"Wind-down: TV turned off (dry_run={dry_run})")

    if light_state == "on":
        if dry_run:
            print("  [DRY RUN] Would dim bedroom light to 20%")
        else:
            requests.post(
                f"{ha_url}/api/services/light/turn_on",
                headers=headers,
                json={
                    "entity_id": "light.master_bedroom_light_1",
                    "brightness_pct": 20
                }
            )
        actions_taken.append("bedroom light dimmed to 20%")
        log(f"Wind-down: bedroom light dimmed (dry_run={dry_run})")

    if therm_state == "cool":
        if dry_run:
            print("  [DRY RUN] Would set thermostat to 69°F")
        else:
            requests.post(
                f"{ha_url}/api/services/climate/set_temperature",
                headers=headers,
                json={
                    "entity_id": "climate.master_bedroom",
                    "temperature": 69
                }
            )
        actions_taken.append("thermostat set to 69°F for sleep")
        log(f"Wind-down: thermostat set to 69F (dry_run={dry_run})")

    if not actions_taken:
        print("  [WIND-DOWN] Nothing to do — all devices already in sleep state")
        log("Wind-down: nothing to do")
        return

    prompt = f"""You are a smart home comfort assistant.

It is {hour}:00 and the following late night wind-down actions were just taken:
{chr(10).join(f'- {a}' for a in actions_taken)}

Generate a short friendly 2-3 sentence message for the homeowner explaining
what the system did and why it helps with sleep quality and energy savings.

RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT:
CATEGORY: COMFORT
PRIORITY: LOW
RECOMMENDATION: [2-3 sentences — friendly, mention specific actions taken]
REASONING: [1 sentence — mention sleep quality and energy benefit]
SMS: NO
SMS_MESSAGE:
"""

    log("Calling Mistral for wind-down recommendation...")
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "stream": False
    })

    raw    = response.json()["response"].strip()
    result = parse_response(raw)

    if result["recommendation"]:
        print(f"\n[WIND-DOWN] {result['recommendation']}")
        print(f"[REASONING] {result['reasoning']}\n")
        log(f"Wind-down recommendation: {result['recommendation']}")
# ----------------------------------------------------------------
# Auto-detect mode from home_snapshot
# ----------------------------------------------------------------
def detect_mode(cursor):
    cursor.execute("""
        SELECT COUNT(*) as count FROM home_snapshot 
        WHERE anomaly_detected = TRUE
    """)
    result = cursor.fetchone()
    return "alert" if result["count"] > 0 else "routine"

# ----------------------------------------------------------------
# Shared schedule helper
# ----------------------------------------------------------------
def _is_scheduled(dev, current_hour, is_weekend):
    in_hours = (dev["active_hours_start"] <= current_hour <= dev["active_hours_end"])
    days_ok = (
        dev["active_days"] == "daily"
        or (dev["active_days"] == "weekends" and is_weekend)
        or (dev["active_days"] == "weekdays" and not is_weekend)
    )
    return in_hours and days_ok


# ----------------------------------------------------------------
# Alert context — lean: only the flagged anomalies + their baselines
# ----------------------------------------------------------------
def build_alert_context(cursor):
    current_hour = datetime.now().hour
    current_day = datetime.now().strftime("%A")
    is_weekend = current_day in ("Saturday", "Sunday")
    is_night = current_hour >= 23 or current_hour < 6

    # Anomalous rooms with their active devices
    cursor.execute("""
        SELECT r.name AS room_name, s.active_devices,
               s.power_trend, s.anomaly_reason
        FROM home_snapshot s
        JOIN rooms r ON s.room_id = r.id
        WHERE s.anomaly_detected = TRUE
        ORDER BY s.power_trend DESC
    """)
    raw_anomalies = cursor.fetchall()

    anomalies = []
    for row in raw_anomalies:
        devices = row["active_devices"]
        if isinstance(devices, str):
            try:
                devices = json.loads(devices)
            except Exception:
                devices = []
        devices = devices or []

        # Baseline for this room at this hour
        cursor.execute("""
            SELECT a.avg_power_this_hour
            FROM home_analytics a
            JOIN rooms r ON a.room_id = r.id
            WHERE r.name = %s AND a.hour_of_day = %s
        """, (row["room_name"], current_hour))
        baseline_row = cursor.fetchone()
        baseline_w = round(float(baseline_row["avg_power_this_hour"]), 1) if baseline_row else None

        # Fetch schedule for each active device
        enriched_devices = []
        for d in devices:
            if not d.get("power", 0):
                continue
            cursor.execute("""
                SELECT dp.active_hours_start, dp.active_hours_end, dp.active_days
                FROM device_profiles dp
                JOIN devices dev ON dp.device_id = dev.id
                WHERE dev.name = %s
                LIMIT 1
            """, (d["name"],))
            prof = cursor.fetchone()
            scheduled = _is_scheduled(prof, current_hour, is_weekend) if prof else None
            enriched_devices.append({
                "name": d["name"],
                "current_power_W": round(d["power"], 1),
                "scheduled": scheduled,
                "schedule": f"{prof['active_hours_start']}:00–{prof['active_hours_end']}:00 {prof['active_days']}" if prof else "unknown"
            })

        anomalies.append({
            "room": row["room_name"],
            "current_power_W": round(row["power_trend"], 1),
            "baseline_W": baseline_w,
            "multiplier": round(row["power_trend"] / baseline_w, 1) if baseline_w else None,
            "anomaly_reason": row["anomaly_reason"],
            "active_devices": enriched_devices
        })

    return {
        "current_hour": current_hour,
        "current_day": current_day,
        "is_night": is_night,
        "anomalies": anomalies
    }


# ----------------------------------------------------------------
# Recommendation context — rich: full baselines, schedules, lifetime usage
# ----------------------------------------------------------------
def build_recommendation_context(cursor):
    current_hour = datetime.now().hour
    current_day = datetime.now().strftime("%A")
    is_weekend = current_day in ("Saturday", "Sunday")

    # Device inventory with schedule flags
    cursor.execute("""
        SELECT dp.device_name, dp.active_hours_start,
               dp.active_hours_end, dp.active_days, r.name AS room_name
        FROM device_profiles dp
        JOIN devices d ON dp.device_id = d.id
        JOIN rooms r ON d.room_id = r.id
        ORDER BY r.id, dp.device_id
    """)
    device_inventory = cursor.fetchall()
    for dev in device_inventory:
        dev["currently_scheduled"] = _is_scheduled(dev, current_hour, is_weekend)

    # Top 10 rooms by current power
    cursor.execute("""
        SELECT r.name AS room_name, s.active_devices,
               s.power_trend, s.anomaly_reason
        FROM home_snapshot s
        JOIN rooms r ON s.room_id = r.id
        ORDER BY s.power_trend DESC
        LIMIT 10
    """)
    snapshot = cursor.fetchall()
    for row in snapshot:
        if isinstance(row["active_devices"], str):
            row["active_devices"] = json.loads(row["active_devices"])

    # All rooms at this hour with trend curve
    cursor.execute("""
        SELECT r.name AS room_name, a.avg_power_this_hour,
               a.total_kwh, a.weekly_pattern
        FROM home_analytics a
        JOIN rooms r ON a.room_id = r.id
        WHERE a.hour_of_day = %s
        ORDER BY a.total_kwh DESC
    """, (current_hour,))
    analytics_rows = cursor.fetchall()

    room_baselines = []
    for row in analytics_rows:
        wp = row["weekly_pattern"]
        if isinstance(wp, str):
            try:
                wp = json.loads(wp)
            except Exception:
                wp = {}
        trend_curve = {
            str(h): round(wp[str(h)], 1)
            for h in range(max(0, current_hour - 3), min(24, current_hour + 4))
            if str(h) in wp and isinstance(wp[str(h)], (int, float))
        }
        room_baselines.append({
            "room_name": row["room_name"],
            "avg_power_this_hour_W": row["avg_power_this_hour"],
            "total_kwh_historical": row["total_kwh"],
            "hourly_trend_curve": trend_curve
        })

    # Lifetime kWh by room
    cursor.execute("""
        SELECT r.name AS room_name, SUM(a.total_kwh) AS lifetime_kwh
        FROM home_analytics a
        JOIN rooms r ON a.room_id = r.id
        GROUP BY r.id, r.name
        ORDER BY lifetime_kwh DESC
    """)
    lifetime_usage = cursor.fetchall()

    return {
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "current_hour": current_hour,
        "current_day": current_day,
        "is_weekend": is_weekend,
        "device_inventory": device_inventory,
        "active_rooms": snapshot,
        "room_baselines": {
            "note": "hourly_trend_curve keys are hours-of-day (0–23), not days of week",
            "data": room_baselines
        },
        "lifetime_kwh_by_room": lifetime_usage
    }

# ----------------------------------------------------------------
# Build prompts
# ----------------------------------------------------------------
def build_alert_prompt(context):
    anomalies = context.get("anomalies", [])

    if not anomalies:
        # Should not happen in normal flow — guard against empty runs
        return None

    # Render each anomaly as plain readable text
    lines = []
    for a in anomalies:
        lines.append(f"Room: {a['room']}")
        lines.append(f"  Current power: {a['current_power_W']}W")
        lines.append(f"  Historical baseline (this hour): {a['baseline_W']}W")
        lines.append(f"  Multiplier above baseline: {a['multiplier']}x")
        lines.append(f"  Trigger reason: {a['anomaly_reason']}")
        for d in a.get("active_devices", []):
            sched = "ON schedule" if d["scheduled"] else "OFF schedule"
            lines.append(f"  Device: {d['name']} — {d['current_power_W']}W — {sched} (profile: {d['schedule']})")
        lines.append("")

    anomaly_block = "\n".join(lines).strip()

    return f"""You are a smart home monitoring agent. Classify the anomaly below and decide if an SMS alert is needed.

Time: {context['current_day']} {context['current_hour']}:00  |  Night hours (11pm–6am): {context['is_night']}

--- ANOMALY ---
{anomaly_block}

--- CLASSIFICATION ---
Choose ALERT_TYPE:
  SECURITY — motion at night (11pm–6am) or motion + sound spike
  FAULT    — device is OFF schedule (see above) OR drawing 8x+ baseline
  ENERGY   — spike during expected active hours, explainable by use

Choose SEVERITY using these rules in order — use the first rule that matches:
  Rule 1: Night hours = True AND any device is OFF schedule → SEVERITY: HIGH
  Rule 2: Any device draws 8x or more above its baseline → SEVERITY: HIGH
  Rule 3: ALERT_TYPE is SECURITY → SEVERITY: HIGH
  Rule 4: Device is OFF schedule during active (daytime) hours → SEVERITY: MEDIUM
  Rule 5: Spike during expected usage hours, device on schedule → SEVERITY: LOW

--- SMS RULE ---
SMS = YES if ALL of the following are true:
  - SEVERITY is HIGH
  - AND one of:
      * ALERT_TYPE is SECURITY
      * ALERT_TYPE is FAULT and the device is OFF schedule
      * Night hours = True (current_hour between 23 and 6)

Otherwise SMS = NO.

--- RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT ---
ALERT_TYPE: [SECURITY | FAULT | ENERGY]
SEVERITY: [HIGH | MEDIUM | LOW]
ALERT: [2-3 sentences — use the exact room and device names from the anomaly above]
REASONING: [1 sentence — cite the exact watts and baseline from the anomaly above]
SMS: [YES or NO]
SMS_MESSAGE: [under 140 chars if YES, blank if NO]
"""


def build_recommendation_prompt(context):
    ctx = json.dumps(context, indent=2, default=str)
    return f"""You are a smart home energy advisor performing a scheduled efficiency review.

Home Context:
{ctx}

--- IMPORTANT DATA NOTES ---
- device_inventory.currently_scheduled = false means this device is active OUTSIDE its defined schedule right now
- room_baselines.hourly_trend_curve keys are hours-of-day (0–23), not days of week
- lifetime_kwh_by_room shows all-time consumption — use this to find chronic high consumers beyond just Kitchen
- current_day and is_weekend tell you whether today matches a device's active_days profile

--- YOUR GOAL ---
Surface one high-value, actionable recommendation. Do NOT default to whichever room has the highest
instantaneous power — look for scheduling mismatches and waste patterns.

--- WHAT TO LOOK FOR (in priority order) ---
1. Any device where currently_scheduled = false but it appears in active_rooms with non-zero power
2. A room in lifetime_kwh_by_room with high total kWh relative to its device profiles
3. A device consistently above its room's average at this hour
4. Rooms drawing power when all their device schedules have ended
5. Weekend-only or weekday-only devices active on the wrong day type

--- RULES ---
- Name the exact device and room, cite specific watt numbers
- Give a concrete action: schedule change, standby disable, or usage adjustment
- Do not say "consider reducing kitchen usage" — that is not actionable
- Motion probability of 0.5 is a sensor artifact, ignore it
- PRIORITY HIGH = off-schedule device running now, or saves >5 kWh/week
- PRIORITY MEDIUM = scheduling gap or consistent overuse, fixable with a profile change
- PRIORITY LOW = minor standby draw or marginal gain

--- RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT ---
CATEGORY: [SCHEDULING | EFFICIENCY | STANDBY | BEHAVIORAL]
PRIORITY: [HIGH | MEDIUM | LOW]
RECOMMENDATION: [2-3 sentences — device name, room, specific action, expected impact]
REASONING: [1-2 sentences — cite the exact signal: currently_scheduled, watts vs baseline, or lifetime_kwh rank]
SMS: NO
SMS_MESSAGE:
"""

# ----------------------------------------------------------------
# Parse plain text response from Mistral
# ----------------------------------------------------------------
def parse_response(raw):
    result = {
        "alert_type": None,
        "severity": None,
        "alert": None,
        "category": None,
        "priority": None,
        "recommendation": None,
        "reasoning": None,
        "send_sms": False,
        "sms_message": None
    }

    lines = raw.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("ALERT_TYPE:"):
            result["alert_type"] = line[len("ALERT_TYPE:"):].strip()
        elif line.startswith("SEVERITY:"):
            result["severity"] = line[len("SEVERITY:"):].strip()
        elif line.startswith("ALERT:"):
            result["alert"] = line[len("ALERT:"):].strip()
        elif line.startswith("CATEGORY:"):
            result["category"] = line[len("CATEGORY:"):].strip()
        elif line.startswith("PRIORITY:"):
            result["priority"] = line[len("PRIORITY:"):].strip()
        elif line.startswith("RECOMMENDATION:"):
            result["recommendation"] = line[len("RECOMMENDATION:"):].strip()
        elif line.startswith("REASONING:"):
            result["reasoning"] = line[len("REASONING:"):].strip()
        elif line.startswith("SMS:"):
            result["send_sms"] = line[len("SMS:"):].strip().upper() == "YES"
        elif line.startswith("SMS_MESSAGE:"):
            msg = line[len("SMS_MESSAGE:"):].strip()
            result["sms_message"] = msg if msg else None

    return result

# ----------------------------------------------------------------
# Call Mistral
# ----------------------------------------------------------------
def run_inference(mode=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Auto-detect mode if not specified
    if mode is None:
        mode = detect_mode(cursor)
        log(f"Auto-detected mode: {mode}")

    if mode == "alert":
        context = build_alert_context(cursor)
        prompt = build_alert_prompt(context)
        if prompt is None:
            log("Alert mode skipped — no active anomalies in snapshot")
            cursor.close()
            conn.close()
            return None
    else:
        context = build_recommendation_context(cursor)
        prompt = build_recommendation_prompt(context)

    cursor.close()
    conn.close()

    log(f"Calling Mistral in {mode} mode...")

    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "stream": False
    })

    raw = response.json()["response"].strip()
    result = parse_response(raw)

    # Code-level severity override: off-schedule device at night is always HIGH
    # regardless of what Mistral returned, since the model under-rates these.
    if mode == "alert" and context.get("is_night"):
        has_offschedule = any(
            not d.get("scheduled", True)
            for a in context.get("anomalies", [])
            for d in a.get("active_devices", [])
        )
        if has_offschedule and result["severity"] and result["severity"].upper() != "HIGH":
            log(f"Severity upgraded HIGH: night hours + off-schedule device (was {result['severity']})")
            result["severity"] = "HIGH"

    # Hard safety gate — model can hallucinate SMS: YES on non-HIGH severity.
    # Enforce the rule in code regardless of what Mistral output.
    if result["send_sms"] and result["severity"] and result["severity"].upper() != "HIGH":
        log(f"SMS suppressed: model said YES but severity={result['severity']} (must be HIGH)")
        result["send_sms"] = False
        result["sms_message"] = None

    # Log everything
    log("=" * 50)
    log(f"MODE: {mode}")
    log(f"RAW RESPONSE:\n{raw}")
    if result["alert_type"]:
        log(f"ALERT_TYPE: {result['alert_type']}")
    if result["severity"]:
        log(f"SEVERITY: {result['severity']}")
    if result["alert"]:
        log(f"ALERT: {result['alert']}")
    if result["category"]:
        log(f"CATEGORY: {result['category']}")
    if result["priority"]:
        log(f"PRIORITY: {result['priority']}")
    if result["recommendation"]:
        log(f"RECOMMENDATION: {result['recommendation']}")
    if result["reasoning"]:
        log(f"REASONING: {result['reasoning']}")
    log(f"SEND SMS: {result['send_sms']}")
    if result["sms_message"]:
        log(f"SMS MESSAGE: {result['sms_message']}")
    log("=" * 50)

    # Send SMS if Mistral decided it's warranted
    if result["send_sms"] and result["sms_message"]:
        send_sms(result["sms_message"])

    return result

# ----------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------
if __name__ == "__main__":
    if PAUSED:
        log("inference.py is paused — set PAUSED = False to re-enable")
        exit(0)

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["alert", "routine"], default=None)
    args = parser.parse_args()

    if args.mode:
        # Called with explicit mode (e.g. from trigger.py --mode alert)
        run_inference(args.mode)
    else:
        # Called by cron — always run routine
        # Also run alert if anomalies exist
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as count FROM home_snapshot WHERE anomaly_detected = TRUE")
        result = cursor.fetchone()
        has_anomaly = result["count"] > 0
        cursor.close()
        conn.close()

        # Check if washer or dryer just finished
        check_laundry_done()
        
        check_laundry_cheap_hours()
        
        check_sprinkler(dry_run=True)
        
        check_late_night_winddown(dry_run=True)
        
        check_security(dry_run=True) 

        # Always run routine
        run_inference("routine")

        # Additionally run alert if anomalies detected
        if has_anomaly:
            run_inference("alert")