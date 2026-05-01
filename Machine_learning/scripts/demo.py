#!/usr/bin/env python3
"""
EcoNest Inference Demo
───────────────────────────────────────────────────────────────
Usage:
  python3 demo.py                                        # uses .env defaults
  python3 demo.py --phone 5307606746 --carrier tmobile
  python3 demo.py --phone 6195551234 --carrier verizon
  python3 demo.py --gateway 9725551234@mms.att.net       # AT&T or unknown carrier

Supported --carrier values:
  tmobile, verizon, cricket, boost, metro, sprint, uscellular, virgin

AT&T note:
  AT&T deprecated txt.att.net. Try --gateway NUMBER@mms.att.net
  If that bounces too, AT&T no longer supports email-to-SMS for that number.
"""

import sys, os, json, argparse, time, smtplib
sys.path.insert(0, '/Users/econest/scripts')

import requests
from email.mime.text import MIMEText
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(os.path.expanduser("~/Desktop/Econest/.env"))

from inference import (
    get_connection,
    build_alert_context, build_alert_prompt,
    build_recommendation_context, build_recommendation_prompt,
    parse_response
)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "mistral"

CARRIERS = {
    "tmobile":    "tmomail.net",
    "t-mobile":   "tmomail.net",
    "verizon":    "vtext.com",
    "cricket":    "sms.cricketwireless.net",
    "boost":      "sms.myboostmobile.com",
    "metro":      "mymetropcs.com",
    "metropcs":   "mymetropcs.com",
    "sprint":     "messaging.sprintpcs.com",
    "uscellular": "email.uscc.net",
    "virgin":     "vmobl.com",
    "consumer":   "mailmymobile.net",
}

# ── Helpers ─────────────────────────────────────────────────────

def send_sms_demo(message, sms_to):
    gmail    = os.environ.get("SMS_GMAIL")
    password = os.environ.get("SMS_GMAIL_APP_PASSWORD")
    if not all([gmail, password, sms_to]):
        print("  [SMS] Missing credentials — check .env")
        return False
    try:
        msg = MIMEText(message)
        msg["From"]    = gmail
        msg["To"]      = sms_to
        msg["Subject"] = ""
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(gmail, password)
            s.sendmail(gmail, sms_to, msg.as_string())
        return True
    except Exception as e:
        print(f"  [SMS] Failed: {e}")
        return False

def call_mistral(prompt):
    r = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "stream": False})
    return r.json()["response"].strip()

def apply_gate(result):
    if result["send_sms"] and result.get("severity", "").upper() != "HIGH":
        result["send_sms"]    = False
        result["sms_message"] = None
        result["_suppressed"] = True
    else:
        result["_suppressed"] = False
    return result

def header(text):
    w = 64
    print(f"\n{'═'*w}")
    print(f"  {text}")
    print(f"{'═'*w}")

def section(text):
    print(f"\n  ── {text}")

def field(label, value):
    print(f"  {label:<14} {value}")

def pause():
    time.sleep(0.5)


# ── Cross-home context ───────────────────────────────────────────

def get_home_context():
    """
    Pulls live cross-home context from HA and DB.
    Injected into every scene's Mistral prompt for full home awareness.
    """
    from inference import get_ha_entity_state, get_cheap_hours

    pricing       = get_cheap_hours()
    motion_front  = get_ha_entity_state("binary_sensor.hobeian_zg_204zl")
    motion_garage = get_ha_entity_state("binary_sensor.motion_sensor_garage")
    person_home   = get_ha_entity_state("person.econest")
    occupied      = person_home == "home" or motion_front == "on" or motion_garage == "on"

    garage1     = get_ha_entity_state("cover.garage12")
    garage2     = get_ha_entity_state("cover.garage_door_3")
    garage_open = garage1 == "open" or garage2 == "open"

    therm_living  = get_ha_entity_state("climate.south_side_and_living_room")
    therm_media   = get_ha_entity_state("climate.media_room")
    therm_bedroom = get_ha_entity_state("climate.master_bedroom")
    temp_living   = get_ha_entity_state("sensor.south_side_and_living_room_temperature")
    temp_media    = get_ha_entity_state("sensor.media_room_temperature")
    temp_bedroom  = get_ha_entity_state("sensor.master_bedroom_temperature")
    hum_living    = get_ha_entity_state("sensor.south_side_and_living_room_humidity")
    hum_media     = get_ha_entity_state("sensor.media_room_humidity")
    hum_bedroom   = get_ha_entity_state("sensor.master_bedroom_humidity")

    lights = {
        "bedroom_1":         get_ha_entity_state("light.bedroom_1_light_1"),
        "bedroom_2":         get_ha_entity_state("light.bedroom_2_light_1"),
        "master_bedroom":    get_ha_entity_state("light.master_bedroom_light_1"),
        "hallway":           get_ha_entity_state("light.hallway_kids_light_1"),
        "living_room":       get_ha_entity_state("light.sd_livingroom_light_1"),
        "study":             get_ha_entity_state("light.sd_study_light_1"),
        "media_room":        get_ha_entity_state("light.upstairs_media_light_1"),
        "outdoor_front":     get_ha_entity_state("light.outside_front_light_1"),
        "permanent_outdoor": get_ha_entity_state("light.permanent_lights"),
    }
    lights_on = [name for name, state in lights.items() if state == "on"]

    tv_state     = get_ha_entity_state("media_player.samsung_q70_series_55")
    washer_state = get_ha_entity_state("sensor.washer_machine_state")
    washer_job   = get_ha_entity_state("sensor.washer_job_state")
    washer_power = get_ha_entity_state("sensor.washer_power")
    dryer_state  = get_ha_entity_state("sensor.dryer_machine_state")
    dryer_power  = get_ha_entity_state("sensor.dryer_power")

    # ── Emporia Vue — labeled circuit power
    emporia = {
        "AC_1":           get_ha_entity_state("sensor.breaker_3_power_minute_average"),
        "AC_2":           get_ha_entity_state("sensor.breaker_11_power_minute_average"),
        "Washer":         get_ha_entity_state("sensor.sp8_power_minute_average"),
        "Dryer":          get_ha_entity_state("sensor.breaker_10_power_minute_average"),
        "Fridge":         get_ha_entity_state("sensor.breaker_4_power_minute_average"),
        "Oven":           get_ha_entity_state("sensor.breaker_2_power_minute_average"),
        "Microwave":      get_ha_entity_state("sensor.breaker_7_power_minute_average"),
        "Dishwasher":     get_ha_entity_state("sensor.disposal_power_minute_average"),
        "Kitchen_Lights": get_ha_entity_state("sensor.breaker_6_power_minute_average"),
        "Master_Bed":     get_ha_entity_state("sensor.breaker_8_power_minute_average"),
        "TV_Master_Bed":  get_ha_entity_state("sensor.sp6_power_minute_average"),
        "TV_Living_Room": get_ha_entity_state("sensor.sp5_power_minute_average"),
        "Xbox":           get_ha_entity_state("sensor.xbox_power_minute_average"),
        "Room_2":         get_ha_entity_state("sensor.breaker_13_power_minute_average"),
        "Garage":         get_ha_entity_state("sensor.breaker_12_power_minute_average"),
        "Bedroom_1_PC":   get_ha_entity_state("sensor.sp7_power_minute_average"),
        "Total":          get_ha_entity_state("sensor.balance_power_minute_average"),
    }

    soil_moisture   = get_ha_entity_state("sensor.wifi_soil_sensor_humidity")
    weather         = get_ha_entity_state("weather.forecast_home")
    next_side_lawn  = get_ha_entity_state("sensor.side_r_lawn_next_cycle")
    next_back_lawn  = get_ha_entity_state("sensor.back_lawn_next_cycle")
    next_back_porch = get_ha_entity_state("sensor.back_porch_lawn_next_cycle")

    # ── DB — historical baselines + anomaly flags + schedule violations
    try:
        conn         = get_connection()
        cur          = conn.cursor(dictionary=True)
        current_hour = datetime.now().hour
        current_day  = datetime.now().strftime("%A")
        is_weekend   = current_day in ("Saturday", "Sunday")

        # Anomaly flags
        cur.execute("""
            SELECT r.name, hs.anomaly_detected, hs.anomaly_reason
            FROM home_snapshot hs
            JOIN rooms r ON hs.room_id = r.id
            WHERE hs.anomaly_detected = TRUE
        """)
        anomaly_rows    = cur.fetchall()
        has_anomaly     = len(anomaly_rows) > 0
        anomaly_summary = ", ".join(
            f"{r['name']} ({r['anomaly_reason']})"
            for r in anomaly_rows
            if r['name'] != "Other"
        ) if anomaly_rows else "none"

        has_anomaly = any(r["anomaly_detected"] for r in anomaly_rows if r['name'] != "Other")

        # Historical baselines this hour
        cur.execute("""
            SELECT r.name AS room_name,
                   a.avg_power_this_hour,
                   a.total_kwh
            FROM home_analytics a
            JOIN rooms r ON a.room_id = r.id
            WHERE a.hour_of_day = %s
            ORDER BY a.avg_power_this_hour DESC
            LIMIT 6
        """, (current_hour,))
        baselines        = cur.fetchall()
        baseline_summary = ", ".join(
            f"{r['room_name']} avg={float(r['avg_power_this_hour']):.0f}W"
            for r in baselines
            if r["avg_power_this_hour"] and float(r["avg_power_this_hour"]) > 5
        )

        # Device schedule violations
        cur.execute("""
            SELECT dp.device_name, r.name AS room_name,
                   dp.active_hours_start, dp.active_hours_end, dp.active_days
            FROM device_profiles dp
            JOIN devices d ON dp.device_id = d.id
            JOIN rooms r ON d.room_id = r.id
        """)
        profiles     = cur.fetchall()
        off_schedule = []
        for p in profiles:
            in_hours = p["active_hours_start"] <= current_hour <= p["active_hours_end"]
            days_ok  = (
                p["active_days"] == "daily"
                or (p["active_days"] == "weekends" and is_weekend)
                or (p["active_days"] == "weekdays" and not is_weekend)
            )
            if not (in_hours and days_ok):
                off_schedule.append(f"{p['device_name']} ({p['room_name']})")

        cur.close()
        conn.close()

    except Exception:
        has_anomaly      = False
        anomaly_summary  = "unavailable"
        baseline_summary = "unavailable"
        off_schedule     = []

    return {
        "time":             datetime.now().strftime("%I:%M %p"),
        "day":              datetime.now().strftime("%A"),
        "pricing":          pricing,
        "occupied":         occupied,
        "person_home":      person_home,
        "motion_front":     motion_front,
        "motion_garage":    motion_garage,
        "garage_open":      garage_open,
        "garage1":          garage1,
        "garage2":          garage2,
        "therm_bedroom":    therm_bedroom,
        "therm_living":     therm_living,
        "therm_media":      therm_media,
        "temp_bedroom":     temp_bedroom,
        "temp_living":      temp_living,
        "temp_media":       temp_media,
        "hum_bedroom":      hum_bedroom,
        "hum_living":       hum_living,
        "hum_media":        hum_media,
        "lights_on":        lights_on,
        "lights":           lights,
        "tv_state":         tv_state,
        "washer_state":     washer_state,
        "washer_job":       washer_job,
        "washer_power":     washer_power,
        "dryer_state":      dryer_state,
        "dryer_power":      dryer_power,
        "emporia":          emporia,
        "has_anomaly":      has_anomaly,
        "anomaly_summary":  anomaly_summary,
        "baseline_summary": baseline_summary,
        "off_schedule":     off_schedule,
        "soil_moisture":    soil_moisture,
        "weather":          weather,
        "next_side_lawn":   next_side_lawn,
        "next_back_lawn":   next_back_lawn,
        "next_back_porch":  next_back_porch,
    }


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _format_watering_time(iso_str):
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(iso_str)
        local = dt.astimezone()
        return local.strftime("%a %b %-d at %-I:%M%p")
    except Exception:
        return iso_str


def format_home_context(ctx):
    emporia = ctx.get("emporia", {})

    # Active circuits > 5W, sorted by watts descending
    active_circuits = {
        k: round(_safe_float(v), 0)
        for k, v in emporia.items()
        if k != "Total" and _safe_float(v) > 5
    }
    circuit_lines = "  |  ".join(
        f"{k}: {v:.0f}W"
        for k, v in sorted(active_circuits.items(), key=lambda x: x[1], reverse=True)
    )

    total        = round(_safe_float(emporia.get("Total", 0)))
    off_schedule = ", ".join(ctx["off_schedule"][:5]) if ctx.get("off_schedule") else "none"

    return f"""
┌─ HOME STATE ─── {ctx['time']} on {ctx['day']} ──────────────────────────────
│
│  OCCUPANCY    {'HOME' if ctx['occupied'] else 'AWAY'}
│               person={ctx['person_home']}  |  front door={ctx['motion_front']}  |  garage motion={ctx['motion_garage']}
│
│  SECURITY     Garage 1: {ctx['garage1']}  |  Garage 2: {ctx['garage2']}{'  ⚠ OPEN' if ctx['garage_open'] else ''}
│
│  ELECTRICITY  {ctx['pricing']['current_tier'].upper()} — {ctx['pricing']['cents_per_kwh']}¢/kWh
│               Peak: {ctx['pricing']['peak_hours']}  |  Cheap: {ctx['pricing']['off_peak_hours']}
│
│  POWER        Total: {total}W
│               {circuit_lines}
│
│  ANOMALIES    {'⚠  ' + ctx.get('anomaly_summary', '') if ctx['has_anomaly'] else 'none'}
│
│  OFF-SCHEDULE {off_schedule}
│
│  THERMOSTATS  Master Bedroom : {ctx['therm_bedroom']}  {ctx['temp_bedroom']}°F  {ctx['hum_bedroom']}% humidity
│               Living Room    : {ctx['therm_living']}  {ctx['temp_living']}°F  {ctx['hum_living']}% humidity
│               Media Room     : {ctx['therm_media']}  {ctx['temp_media']}°F  {ctx['hum_media']}% humidity
│
│  LIGHTS ON    {', '.join(ctx['lights_on']) if ctx['lights_on'] else 'none'}
│
│  APPLIANCES   Washer : {ctx['washer_state']} ({ctx['washer_job']})  {round(_safe_float(emporia.get('Washer', 0)))}W
│               Dryer  : {ctx['dryer_state']}  {round(_safe_float(emporia.get('Dryer', 0)))}W
│               TV Bedroom   : {round(_safe_float(emporia.get('TV_Master_Bed', 0)))}W
│               TV Living Rm : {round(_safe_float(emporia.get('TV_Living_Room', 0)))}W
│               AC Unit 1    : {round(_safe_float(emporia.get('AC_1', 0)))}W
│               AC Unit 2    : {round(_safe_float(emporia.get('AC_2', 0)))}W
│
│  GARDEN       Soil moisture : {ctx['soil_moisture']}%
│               Weather       : {ctx['weather']}
│               Side lawn     : {_format_watering_time(ctx['next_side_lawn'])}
│               Back lawn     : {_format_watering_time(ctx['next_back_lawn'])}
│
└──────────────────────────────────────────────────────────────────
""".strip()

# ── Scenes ──────────────────────────────────────────────────────

def scene_recommendation():
    header("SCENE 1 of 6 — Routine Recommendation  (runs every 30 min via cron)")
    print("  Every 30 minutes the system reviews long-term energy patterns")
    print("  and surfaces one actionable recommendation. No SMS.")

    section("Gathering full home context...")
    ctx          = get_home_context()
    home_ctx_str = format_home_context(ctx)
    print(home_ctx_str)

    section("Building recommendation context from DB...")
    conn   = get_connection()
    cur    = conn.cursor(dictionary=True)
    db_ctx = build_recommendation_context(cur)
    cur.close(); conn.close()
    print(f"  {len(db_ctx['device_inventory'])} devices  |  {len(db_ctx['active_rooms'])} active rooms  |  {db_ctx['current_day']} {db_ctx['current_hour']}:00")

    section("Sending to Mistral (local — no cloud, no data leaves the house)...")

    prompt = f"""You are a smart home energy advisor performing a scheduled efficiency review.

--- LIVE HOME STATE ---
{home_ctx_str}

--- HISTORICAL DB CONTEXT ---
{json.dumps(db_ctx, indent=2, default=str)}

--- IMPORTANT DATA NOTES ---
- device_inventory.currently_scheduled = false means this device is active OUTSIDE its defined schedule right now
- room_baselines.hourly_trend_curve keys are hours-of-day (0-23), not days of week
- lifetime_kwh_by_room shows all-time consumption
- Use BOTH the live home state AND the DB context together to surface the best recommendation
- The live home state has real-time thermostat temps, lights, washer/dryer, breaker-level watts, occupancy

--- YOUR GOAL ---
Surface one high-value, actionable recommendation. Cross-reference the live home state
with historical patterns. Look for combinations such as washer running during peak hours,
hot room with AC running, lights on in empty rooms, garage door open, etc.

--- WHAT TO LOOK FOR (in priority order) ---
1. Any device where currently_scheduled = false but it appears in active_rooms with non-zero power
2. Live home state shows something anomalous — hot room, lights on while away, laundry during peak
3. A room in lifetime_kwh_by_room with high total kWh relative to its device profiles
4. Rooms drawing power when all their device schedules have ended
5. Weekend-only or weekday-only devices active on the wrong day type

--- RULES ---
- Name the exact device and room, cite specific watt numbers
- Give a concrete action: schedule change, standby disable, or usage adjustment
- PRIORITY HIGH = off-schedule device running now, or saves >5 kWh/week
- PRIORITY MEDIUM = scheduling gap or consistent overuse
- PRIORITY LOW = minor standby draw or marginal gain

--- RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT ---
CATEGORY: [SCHEDULING | EFFICIENCY | STANDBY | BEHAVIORAL]
PRIORITY: [HIGH | MEDIUM | LOW]
RECOMMENDATION: [2-3 sentences — device name, room, specific action, expected impact]
REASONING: [1-2 sentences — cite exact signal from either live state or DB context]
SMS: NO
SMS_MESSAGE:
"""

    raw    = call_mistral(prompt)
    result = parse_response(raw)

    section("Mistral response")
    field("CATEGORY:",    result["category"] or "—")
    field("PRIORITY:",    result["priority"]  or "—")
    print()
    print(f"    {result['recommendation']}")
    print()
    field("REASONING:", "")
    print(f"    {result['reasoning']}")
    print()
    field("SMS:", "NO  (recommendations never page you)")
    pause()


def scene_energy_alert(sms_to):
    header("SCENE 2 of 6 — Energy Anomaly Alert  (live anomalies + 2am simulation)")
    print("  Rooms currently flagged above 4x their baseline are pulled")
    print("  directly from the house. The model classifies the anomaly,")
    print("  then simulates what happens if the same thing occurs at 2am.")

    conn      = get_connection()
    cur       = conn.cursor(dictionary=True)
    alert_ctx = build_alert_context(cur)
    cur.close(); conn.close()

    # Get home context first
    section("Gathering full home context...")
    ctx          = get_home_context()
    home_ctx_str = format_home_context(ctx)

    # Emporia Vue circuit readings
    emporia = ctx.get("emporia", {})
    active_emporia = {
        k: round(_safe_float(v), 0)
        for k, v in emporia.items()
        if k != "Total" and _safe_float(v) > 50
    }

    # ── PART 1: Current anomalies ────────────────────────────────
    if not alert_ctx["anomalies"]:
        print("\n  No active anomalies in the house right now.")
        print("  (This is the right outcome — system is quiet when nothing is wrong.)")
    else:
        section(f"Active anomalies ({len(alert_ctx['anomalies'])} room(s)):")
        for a in alert_ctx["anomalies"]:
            if a['room'] == "Other":
                continue
            print(f"\n  Room      : {a['room']}")
            print(f"  Power     : {a['current_power_W']}W  (baseline {a['baseline_W']}W  →  {a['multiplier']}x)")
            print(f"  Reason    : {a['anomaly_reason']}")
            for d in a["active_devices"]:
                sched = "on schedule" if d["scheduled"] else "OFF SCHEDULE"
                print(f"  Device    : {d['name']}  {d['current_power_W']}W  [{sched}]")

        print(f"\n  ── Live Emporia Vue circuit readings (>50W):")
        for circuit, watts in sorted(active_emporia.items(), key=lambda x: x[1], reverse=True):
            print(f"  {circuit:<22} {watts:>6.0f}W")

        # Build anomaly block for Mistral
        lines = []
        for a in alert_ctx["anomalies"]:
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

        section("Sending to Mistral (current time)...")

        prompt = f"""You are a smart home monitoring agent. Classify the anomaly below and decide if an SMS alert is needed.

--- LIVE HOME STATE ---
{home_ctx_str}

--- ANOMALY DETAILS ---
Time: {alert_ctx['current_day']} {alert_ctx['current_hour']}:00  |  Night hours (11pm-6am): {alert_ctx['is_night']}

{anomaly_block}

--- CLASSIFICATION ---
Choose ALERT_TYPE:
  SECURITY — motion at night (11pm-6am) or motion + sound spike
  FAULT    — device is OFF schedule OR drawing 8x+ baseline
  ENERGY   — spike during expected active hours, explainable by use

Use the live home state to inform severity. If home is AWAY and anomaly is active,
or if garage is open, or if it's night with no expected occupancy, escalate severity.

Choose SEVERITY using these rules in order:
  Rule 1: Night hours = True AND any device is OFF schedule → SEVERITY: HIGH
  Rule 2: Any device draws 8x or more above its baseline → SEVERITY: HIGH
  Rule 3: ALERT_TYPE is SECURITY → SEVERITY: HIGH
  Rule 4: Home is AWAY and anomaly is active → SEVERITY: HIGH
  Rule 5: Device is OFF schedule during active daytime hours → SEVERITY: MEDIUM
  Rule 6: Spike during expected usage hours, device on schedule → SEVERITY: LOW

--- SMS RULE ---
SMS = YES if SEVERITY is HIGH and one of: SECURITY, FAULT off-schedule, night hours, or home AWAY.
Otherwise SMS = NO.

--- RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT ---
ALERT_TYPE: [SECURITY | FAULT | ENERGY]
SEVERITY: [HIGH | MEDIUM | LOW]
ALERT: [2-3 sentences — use exact room and device names, reference home state if relevant]
REASONING: [1 sentence — cite exact watts, baseline, and any relevant home state factors]
SMS: [YES or NO]
SMS_MESSAGE: [under 140 chars if YES, blank if NO]
"""

        raw    = call_mistral(prompt)
        result = apply_gate(parse_response(raw))

        section("Mistral response (current time)")
        field("ALERT_TYPE:", result["alert_type"] or "—")
        field("SEVERITY:",   result["severity"]   or "—")
        print()
        print(f"    {result['alert']}")
        print()
        field("REASONING:", "")
        print(f"    {result['reasoning']}")
        print()
        if result["send_sms"] and result["sms_message"]:
            field("SMS:", f"YES — would fire  \"{result['sms_message']}\"")
        elif result["_suppressed"]:
            field("SMS:", f"BLOCKED by gate  (severity={result['severity']}, gate requires HIGH)")
        else:
            field("SMS:", "NO")

    # ── PART 2: Same anomaly at 2am ──────────────────────────────
    section("Now simulating: what if this happened at 2am?")
    print("  Same house data — real rooms, real watts — just time-shifted to 2am.")


    # If no anomalies, pull highest power room as what-if
    if not alert_ctx["anomalies"]:
        conn2 = get_connection()
        cur2  = conn2.cursor(dictionary=True)
        cur2.execute("""
            SELECT r.name AS room, hs.power_trend, hs.active_devices,
                   ha.avg_power_this_hour AS baseline_w
            FROM home_snapshot hs
            JOIN rooms r ON hs.room_id = r.id
            JOIN home_analytics ha ON ha.room_id = hs.room_id AND ha.hour_of_day = 2
            WHERE hs.power_trend > 0
            ORDER BY hs.power_trend DESC
            LIMIT 1
        """)
        top = cur2.fetchone()
        cur2.close(); conn2.close()

        if not top:
            print("  No live power data available.")
            pause()
            return

        devices = top["active_devices"]
        if isinstance(devices, str):
            devices = json.loads(devices)

        baseline = round(float(top["baseline_w"]), 1) if top["baseline_w"] else 1.0
        mult     = round(top["power_trend"] / baseline, 1) if baseline > 0 else None

        anomalies_2am = [{
            "room":            top["room"],
            "current_power_W": round(top["power_trend"], 1),
            "baseline_W":      baseline,
            "multiplier":      mult,
            "anomaly_reason":  f"Power {top['power_trend']:.1f}W vs 2am baseline {baseline}W",
            "active_devices": [{
                "name":            d.get("name", "Unknown"),
                "current_power_W": round(d.get("power", 0), 1),
                "scheduled":       False,
                "schedule":        "active hours only"
            } for d in (devices or []) if d.get("power", 0) > 0]
        }]
    else:
        anomalies_2am = alert_ctx["anomalies"]

    # Mark all devices off-schedule at 2am
    for a in anomalies_2am:
        for d in a["active_devices"]:
            d["scheduled"] = False
            if "schedule" not in d:
                d["schedule"] = "not scheduled at 2am"

    print(f"\n  {'─'*56}")
    for a in anomalies_2am:
        if a['room'] == "Other":
            continue
        print(f"  Room      : {a['room']}")
        print(f"  Power     : {a['current_power_W']}W  (2am baseline {a['baseline_W']}W  →  {a['multiplier']}x)")
        for d in a["active_devices"]:
            print(f"  Device    : {d['name']}  {d['current_power_W']}W  [OFF schedule at 2am]")
    print(f"  {'─'*56}")

    # Build 2am anomaly block
    lines2 = []
    for a in anomalies_2am:
        lines2.append(f"Room: {a['room']}")
        lines2.append(f"  Current power: {a['current_power_W']}W")
        lines2.append(f"  2am baseline: {a['baseline_W']}W")
        lines2.append(f"  Multiplier: {a['multiplier']}x")
        lines2.append(f"  Trigger reason: {a['anomaly_reason']}")
        for d in a.get("active_devices", []):
            lines2.append(f"  Device: {d['name']} — {d['current_power_W']}W — OFF schedule at 2am")
        lines2.append("")
    anomaly_block_2am = "\n".join(lines2).strip()

    section("Sending to Mistral (2am simulation)...")

    prompt_2am = f"""You are a smart home monitoring agent. This is a 2am simulation.

--- LIVE HOME STATE (time-shifted to 2am) ---
{home_ctx_str}
NOTE: Treat current_hour as 2am for this simulation regardless of actual time shown above.

--- ANOMALY AT 2AM ---
{anomaly_block_2am}

--- CLASSIFICATION ---
At 2am ALL active devices are considered off-schedule unless they are security devices.
Home is in sleep mode — any significant power draw is suspicious.
Use home state context — garage open, person home, motion detected all matter.

SEVERITY at 2am is almost always HIGH if any device is drawing significant power.

SMS = YES if SEVERITY is HIGH. At 2am with off-schedule devices this is almost always the case.

--- RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT ---
ALERT_TYPE: [SECURITY | FAULT | ENERGY]
SEVERITY: [HIGH | MEDIUM | LOW]
ALERT: [2-3 sentences — reference the 2am context and home state]
REASONING: [1 sentence — cite exact watts and why 2am makes this significant]
SMS: [YES or NO]
SMS_MESSAGE: [under 140 chars if YES, blank if NO]
"""

    raw2    = call_mistral(prompt_2am)
    result2 = parse_response(raw2)

    # Override: off-schedule at night always HIGH
    has_offschedule = any(
        not d.get("scheduled", True)
        for a in anomalies_2am
        for d in a.get("active_devices", [])
    )
    if has_offschedule and result2.get("severity", "").upper() != "HIGH":
        result2["severity"] = "HIGH"

    result2 = apply_gate(result2)

    section("Mistral response (2am simulation)")
    field("ALERT_TYPE:", result2["alert_type"] or "—")
    field("SEVERITY:",   result2["severity"]   or "—")
    print()
    print(f"    {result2['alert']}")
    print()
    field("REASONING:", "")
    print(f"    {result2['reasoning']}")
    print()

    if result2["send_sms"] and result2["sms_message"]:
        field("SMS:", f"YES → {sms_to}")
        field("MESSAGE:", f'"{result2["sms_message"]}"')
        section("Firing SMS...")
        ok = send_sms_demo(result2["sms_message"], sms_to)
        if ok:
            print("  Delivered. Check your phone.")
    elif result2["_suppressed"]:
        field("SMS:", f"BLOCKED by gate  (severity={result2['severity']} — Mistral was conservative)")
        print()
        print("  The gate correctly prevented a false alert.")
    else:
        field("SMS:", "Model returned NO")
    pause()


def scene_laundry(sms_to):
    header("SCENE 3 of 6 — Laundry Energy Recommendation  (Mistral + live pricing)")
    print("  System checks if washer/dryer is running during peak hours")
    print("  and generates an AI recommendation with exact dollar savings.")

    from inference import get_cheap_hours, get_ha_entity_state

    section("Current electricity pricing:")
    pricing = get_cheap_hours()
    print(f"  Tier     : {pricing['current_tier'].upper()}")
    print(f"  Rate     : {pricing['cents_per_kwh']}¢/kWh")
    print(f"  Peak hrs : {pricing['peak_hours']}")
    print(f"  Cheap hrs: {pricing['off_peak_hours']}")

    section("Checking washer and dryer state from SmartThings via HA...")
    washer_state = get_ha_entity_state("sensor.washer_machine_state")
    dryer_state  = get_ha_entity_state("sensor.dryer_machine_state")
    print(f"  Washer : {washer_state}")
    print(f"  Dryer  : {dryer_state}")

    # Demo overrides
    washer_running = True
    dryer_running  = False

    active = []
    if washer_running:
        active.append("Washer")
    if dryer_running:
        active.append("Dryer")

    demo_pricing = pricing.copy()
    if demo_pricing["current_tier"] == "off_peak":
        demo_pricing["current_tier"]    = "peak"
        demo_pricing["cents_per_kwh"]   = 18
        demo_pricing["next_cheap_window"] = "after 9pm tonight"
        print("\n  [DEMO] Simulating peak hours for demonstration")

    section("Gathering full home context...")
    ctx          = get_home_context()
    home_ctx_str = format_home_context(ctx)

    section("Sending to Mistral (local — no cloud, no data leaves the house)...")

    prompt = f"""You are a smart home energy advisor.

--- LIVE HOME STATE ---
{home_ctx_str}

--- LAUNDRY SITUATION ---
The following laundry appliances are currently running during {demo_pricing['current_tier']} electricity hours ({demo_pricing['cents_per_kwh']}¢/kWh):
Appliances running: {', '.join(active)}

Key facts:
- Peak hours are {demo_pricing['peak_hours']} at {demo_pricing['cents_per_kwh']}¢/kWh
- Off-peak hours are {demo_pricing['off_peak_hours']} at 8¢/kWh
- Dryer uses ~3.3 kWh per cycle
- Washer uses ~0.5 kWh per cycle
- Next cheap window: {demo_pricing['next_cheap_window']}

Use the live home state to enrich the recommendation. If total home watts are already high,
mention the combined load. If AC is running simultaneously, note the double energy impact.
If home is occupied, personalize the timing suggestion.

Generate a short friendly 2-3 sentence recommendation.
Include the exact cost difference in dollars between running now vs off-peak.
Reference at least one other home state factor beyond just the laundry.

RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT:
CATEGORY: EFFICIENCY
PRIORITY: [HIGH if peak, MEDIUM if mid_peak]
RECOMMENDATION: [2-3 sentences with exact dollar savings and home context reference]
REASONING: [1 sentence citing exact cents/kWh, cycle cost, and one home state factor]
SMS: NO
SMS_MESSAGE:
"""

    raw    = call_mistral(prompt)
    result = parse_response(raw)

    section("Mistral response")
    field("CATEGORY:", result["category"] or "—")
    field("PRIORITY:", result["priority"]  or "—")
    print()
    print(f"    {result['recommendation']}")
    print()
    field("REASONING:", "")
    print(f"    {result['reasoning']}")
    print()
    field("SMS:", "NO  (recommendations never page you)")
    pause()


def scene_sprinkler(sms_to):
    header("SCENE 4 of 6 — Smart Sprinkler Morning Check  (soil + weather + pricing)")
    print("  Every morning at 7am EcoNest checks soil moisture, weather forecast,")
    print("  and electricity pricing to decide whether to water the lawn.")

    from inference import get_ha_entity_state, get_cheap_hours

    section("Live sensor data right now:")
    soil_state    = get_ha_entity_state("sensor.wifi_soil_sensor_humidity")
    weather_state = get_ha_entity_state("weather.forecast_home")
    pricing       = get_cheap_hours()

    try:
        soil_moisture = float(soil_state)
    except (TypeError, ValueError):
        soil_moisture = 0.0

    print(f"  Soil moisture : {soil_moisture}%")
    print(f"  Weather now   : {weather_state}")
    print(f"  Pricing tier  : {pricing['current_tier']} ({pricing['cents_per_kwh']}¢/kWh)")

    section("Pulling hourly rain forecast from Met.no...")
    ha_url   = os.environ.get("HA_URL", "http://localhost:8123")
    ha_token = os.environ.get("HA_TOKEN")
    headers  = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json"
    }

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
        print(f"  Forecast fetch failed: {e}")
        forecasts = []

    rain_conditions = {"rainy", "pouring", "lightning", "lightning-rainy", "hail"}
    upcoming_rain   = any(
        f.get("condition", "") in rain_conditions or f.get("precipitation", 0) > 0.05
        for f in forecasts[:6]
    )
    upcoming_precip = sum(f.get("precipitation", 0) for f in forecasts[:6])

    print(f"  Rain next 6hr : {'yes' if upcoming_rain else 'no'} ({upcoming_precip:.2f} in expected)")

    should_skip = soil_moisture > 60 or upcoming_rain
    good_time   = pricing["current_tier"] == "off_peak"
    needs_water = soil_moisture < 40

    section("Decision:")
    if should_skip:
        reason = []
        if soil_moisture > 60:
            reason.append(f"soil moisture at {soil_moisture}%")
        if upcoming_rain:
            reason.append(f"rain expected ({upcoming_precip:.2f} in next 6hr)")
        print(f"  SKIP — {', '.join(reason)}")
    elif needs_water and not good_time:
        print(f"  DELAY — needs water but {pricing['current_tier']} pricing, wait until 9pm")
    elif needs_water and good_time:
        print(f"  WATER NOW — soil dry and off-peak pricing")
        print(f"  [DRY RUN] Would open valve.side_r_lawn, valve.back_porch_lawn, valve.back_lawn")
    else:
        print(f"  MONITOR — soil at {soil_moisture}%, watching")

    section("Gathering full home context...")
    ctx          = get_home_context()
    home_ctx_str = format_home_context(ctx)

    section("Sending to Mistral (simulating 7am morning check)...")

    prompt = f"""You are a smart home energy and garden advisor performing a 7am morning check.

--- LIVE HOME STATE ---
{home_ctx_str}

--- SPRINKLER SITUATION ---
- Soil moisture: {soil_moisture}%
- Current weather: {weather_state}
- Rain expected in next 6 hours: {'yes' if upcoming_rain else 'no'} ({upcoming_precip:.2f} inches)
- Electricity tier: {pricing['current_tier']} ({pricing['cents_per_kwh']}¢/kWh)
- Next cheap window: {pricing['next_cheap_window']}
- Irrigation pump uses ~500W
- Three zones: side lawn, back porch lawn, back lawn (~15 gallons each = 45 gallons total)

Decision made: {'SKIP watering' if should_skip else 'DELAY until off-peak' if needs_water and not good_time else 'WATER NOW' if needs_water and good_time else 'MONITOR'}

Use the live home state to enrich the recommendation. If total home power is already high,
mention avoiding adding pump load. Reference occupancy or weather if relevant.

Generate a short friendly 2-3 sentence recommendation explaining the decision.
If skipping, mention gallons saved and reference at least one home state factor.
If delaying, mention the cost difference and total home load context.

RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT:
CATEGORY: EFFICIENCY
PRIORITY: [HIGH if needs water, LOW if skipping]
RECOMMENDATION: [2-3 sentences — mention soil %, weather, pricing, and one home state factor]
REASONING: [1 sentence — cite the exact data points from both sprinkler and home state]
SMS: NO
SMS_MESSAGE:
"""

    raw    = call_mistral(prompt)
    result = parse_response(raw)

    section("Mistral response")
    field("CATEGORY:", result["category"] or "—")
    field("PRIORITY:", result["priority"]  or "—")
    print()
    print(f"    {result['recommendation']}")
    print()
    field("REASONING:", "")
    print(f"    {result['reasoning']}")
    print()

    if result["recommendation"]:
        msg = f"[EcoNest Sprinkler] {result['recommendation']}"
        field("SMS:", f"YES → {sms_to}")
        field("MESSAGE:", f'"{msg}"')
        section("Firing SMS...")
        ok = send_sms_demo(msg, sms_to)
        if ok:
            print("  Delivered. Check your phone.")
    pause()


def scene_winddown(sms_to, dry_run=True):
    header("SCENE 5 of 6 — Late Night Wind-Down  (comfort + energy automation)")
    print("  After 11pm EcoNest checks if devices are still running")
    print("  and prepares the home for sleep automatically.")
    print(f"  {'[DRY RUN — no devices will be changed]' if dry_run else '[LIVE MODE — devices will be controlled]'}")

    from inference import get_ha_entity_state

    ha_url   = os.environ.get("HA_URL", "http://localhost:8123")
    ha_token = os.environ.get("HA_TOKEN")
    headers  = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json"
    }

    section("Gathering full home context...")
    ctx          = get_home_context()
    home_ctx_str = format_home_context(ctx)
    print(f"\n{home_ctx_str}\n")

    section("Reading device states for wind-down...")
    tv_state    = ctx["tv_state"]
    light_state = ctx["lights"]["master_bedroom"]
    therm_state = ctx["therm_bedroom"]

    print(f"  Samsung TV       : {tv_state}")
    print(f"  Master Bedroom   : {light_state}")
    print(f"  Thermostat       : {therm_state}")

    actions_taken = []

    if tv_state in ("on", "playing"):
        if dry_run:
            print("  [DRY RUN] Would turn off Samsung TV")
        else:
            requests.post(
                f"{ha_url}/api/services/media_player/turn_off",
                headers=headers,
                json={"entity_id": "media_player.samsung_q70_series_55"}
            )
        actions_taken.append("Samsung TV turned off")

    if light_state == "on":
        if dry_run:
            print("  [DRY RUN] Would dim master bedroom light to 20%")
        else:
            requests.post(
                f"{ha_url}/api/services/light/turn_on",
                headers=headers,
                json={
                    "entity_id": "light.master_bedroom_light_1",
                    "brightness_pct": 20
                }
            )
        actions_taken.append("master bedroom light dimmed to 20%")

    if therm_state == "cool":
        if dry_run:
            print("  [DRY RUN] Would set master bedroom thermostat to 69°F")
        else:
            requests.post(
                f"{ha_url}/api/services/climate/set_temperature",
                headers=headers,
                json={
                    "entity_id": "climate.master_bedroom",
                    "temperature": 69
                }
            )
        actions_taken.append("master bedroom thermostat set to 69°F")

    if not actions_taken:
        print("\n  All devices already in sleep state — nothing to do.")
        return

    section("Actions " + ("planned" if dry_run else "taken") + ":")
    for a in actions_taken:
        print(f"  → {a}")

    section("Sending to Mistral with full home context...")

    prompt = f"""You are a smart home comfort and energy assistant performing a late night wind-down check.

--- LIVE HOME STATE ---
{home_ctx_str}

--- WIND-DOWN ACTIONS {'PLANNED' if dry_run else 'TAKEN'} ---
{chr(10).join(f'- {a}' for a in actions_taken)}

Using the full home context above, generate a short friendly 2-3 sentence message
for the homeowner. Reason about the whole home state — mention occupancy, room temperatures,
energy draw, and what was automated. Do not just describe the actions taken — interpret
what the full home state means for the night ahead. Reference specific sensor values
like room temperatures, who is home, total power draw, and lights status.

RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT:
CATEGORY: COMFORT
PRIORITY: LOW
RECOMMENDATION: [2-3 sentences — reference actual sensor values, not just actions]
REASONING: [1 sentence — cite specific data points from the home state]
SMS: NO
SMS_MESSAGE:
"""

    raw    = call_mistral(prompt)
    result = parse_response(raw)

    section("Mistral response")
    field("CATEGORY:", result["category"] or "—")
    field("PRIORITY:", result["priority"]  or "—")
    print()
    print(f"    {result['recommendation']}")
    print()
    field("REASONING:", "")
    print(f"    {result['reasoning']}")
    print()
    field("SMS:", "NO  (comfort automations don't page you)")
    pause()

def scene_security(sms_to, dry_run=True):
    header("SCENE 6 of 6 — Security Check  (cross-sensor reasoning)")
    print("  EcoNest monitors occupancy, motion sensors, garage doors,")
    print("  power draw, and time of day simultaneously to detect security risks.")
    print("  [SIMULATING: 2:00am]")

    DEMO_HOUR = 2
    is_night  = True

    section("Gathering full home context...")
    ctx          = get_home_context()
    home_ctx_str = format_home_context(ctx)
    print(f"\n{home_ctx_str}\n")

    section("Evaluating security conditions...")

    person_home   = ctx["person_home"]
    motion_front  = ctx["motion_front"]
    motion_garage = ctx["motion_garage"]
    garage_open   = ctx["garage_open"]
    garage1       = ctx["garage1"]
    garage2       = ctx["garage2"]
    occupied      = ctx["occupied"]

    emporia       = ctx.get("emporia", {})
    total_watts   = round(_safe_float(emporia.get("Total", 0)))
    ac1_watts     = round(_safe_float(emporia.get("AC_1", 0)))
    ac2_watts     = round(_safe_float(emporia.get("AC_2", 0)))
    oven_watts    = round(_safe_float(emporia.get("Oven", 0)))
    lights_on     = ctx["lights_on"]
    temp_bedroom  = ctx["temp_bedroom"]
    temp_living   = ctx["temp_living"]
    temp_media    = ctx["temp_media"]

    conditions = []

    # ── Garage door checks
    if garage_open:
        which = []
        if garage1 == "open":
            which.append("Garage Door 1")
        if garage2 == "open":
            which.append("Garage Door 2")
        garage_str = ", ".join(which)

        if not occupied:
            conditions.append({
                "type":     "GARAGE_OPEN_EMPTY",
                "detail":   f"{garage_str} open and home appears empty at {DEMO_HOUR}:00am",
                "severity": "HIGH"
            })
        elif is_night:
            conditions.append({
                "type":     "GARAGE_OPEN_NIGHT",
                "detail":   f"{garage_str} open at {DEMO_HOUR}:00am (night hours)",
                "severity": "HIGH"
            })
        else:
            conditions.append({
                "type":     "GARAGE_OPEN",
                "detail":   f"{garage_str} open",
                "severity": "MEDIUM"
            })
        print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

        # Cross: garage open + AC running = energy waste
        if ac1_watts > 100 or ac2_watts > 100:
            conditions.append({
                "type":     "GARAGE_OPEN_AC_RUNNING",
                "detail":   f"{garage_str} open while AC drawing {ac1_watts + ac2_watts}W — conditioned air escaping",
                "severity": "MEDIUM"
            })
            print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

    # ── Motion checks
    if motion_front == "on":
        conditions.append({
            "type":     "NIGHT_MOTION_FRONT_DOOR",
            "detail":   f"Motion at front door at {DEMO_HOUR}:00am (night hours)",
            "severity": "HIGH"
        })
        print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

    if motion_garage == "on":
        conditions.append({
            "type":     "NIGHT_MOTION_GARAGE",
            "detail":   f"Motion in garage at {DEMO_HOUR}:00am (night hours)",
            "severity": "HIGH"
        })
        print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

    # ── Motion + garage open combo
    if (motion_front == "on" or motion_garage == "on") and garage_open:
        conditions.append({
            "type":     "MOTION_WITH_GARAGE_OPEN",
            "detail":   f"Motion detected while garage door is open at {DEMO_HOUR}:00am",
            "severity": "HIGH"
        })
        print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

    # ── Power while home empty
    if not occupied and total_watts > 500:
        conditions.append({
            "type":     "POWER_WHILE_AWAY",
            "detail":   f"Home appears empty but drawing {total_watts}W at {DEMO_HOUR}:00am",
            "severity": "MEDIUM"
        })
        print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

    # ── Oven running while home empty
    if not occupied and oven_watts > 100:
        conditions.append({
            "type":     "OVEN_HOME_EMPTY",
            "detail":   f"Oven drawing {oven_watts}W at {DEMO_HOUR}:00am while home appears empty — fire risk",
            "severity": "HIGH"
        })
        print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

    # ── Lights on while home empty at night
    if not occupied and lights_on:
        conditions.append({
            "type":     "LIGHTS_HOME_EMPTY",
            "detail":   f"Lights on in {', '.join(lights_on)} while home appears empty at {DEMO_HOUR}:00am",
            "severity": "LOW"
        })
        print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

    # ── Temperature imbalance
    temps = {
        "Master Bedroom": _safe_float(temp_bedroom),
        "Living Room":    _safe_float(temp_living),
        "Media Room":     _safe_float(temp_media),
    }
    valid_temps = {k: v for k, v in temps.items() if v > 0}
    if len(valid_temps) >= 2:
        max_room = max(valid_temps, key=valid_temps.get)
        min_room = min(valid_temps, key=valid_temps.get)
        temp_diff = valid_temps[max_room] - valid_temps[min_room]
        if temp_diff > 6:
            conditions.append({
                "type":     "TEMP_IMBALANCE",
                "detail":   f"{max_room} is {valid_temps[max_room]:.1f}°F — {temp_diff:.1f}°F warmer than {min_room} ({valid_temps[min_room]:.1f}°F)",
                "severity": "LOW"
            })
            print(f"  ⚠ [{conditions[-1]['severity']}] {conditions[-1]['detail']}")

    # ── No real conditions — simulate for demo
    if not conditions:
        print("  No active security conditions detected right now.")
        print("  [DEMO] Simulating garage open + home empty at 2am...")
        conditions.append({
            "type":     "GARAGE_OPEN_EMPTY",
            "detail":   "Garage Door 1 open and home appears empty at 2:00am (simulated)",
            "severity": "HIGH"
        })
        conditions.append({
            "type":     "GARAGE_OPEN_AC_RUNNING",
            "detail":   f"Garage open while AC drawing {ac1_watts + ac2_watts}W — conditioned air escaping (simulated)",
            "severity": "MEDIUM"
        })
        occupied    = False
        person_home = "away"
        garage_open = True
        garage1     = "open"

    severity = "HIGH" if any(c["severity"] == "HIGH" for c in conditions) else \
               "MEDIUM" if any(c["severity"] == "MEDIUM" for c in conditions) else "LOW"

    section(f"Security conditions found: {len(conditions)}  |  Overall severity: {severity}")
    for c in conditions:
        print(f"  [{c['severity']}] {c['type']}: {c['detail']}")

    # ── Automation actions for HIGH severity
    if severity == "HIGH":
        ha_url   = os.environ.get("HA_URL", "http://localhost:8123")
        ha_token = os.environ.get("HA_TOKEN")
        headers  = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}

        automation_actions = []

        # Close any open garage doors
        if garage1 == "open":
            if dry_run:
                print("\n  [DRY RUN] Would close Garage Door 1")
            else:
                requests.post(
                    f"{ha_url}/api/services/cover/close_cover",
                    headers=headers,
                    json={"entity_id": "cover.garage12"}
                )
                print("\n  ✓ Garage Door 1 closed automatically")
            automation_actions.append("Garage Door 1 closed")

        if garage2 == "open":
            if dry_run:
                print("  [DRY RUN] Would close Garage Door 2")
            else:
                requests.post(
                    f"{ha_url}/api/services/cover/close_cover",
                    headers=headers,
                    json={"entity_id": "cover.garage_door_3"}
                )
                print("  ✓ Garage Door 2 closed automatically")
            automation_actions.append("Garage Door 2 closed")

        # Turn on outdoor front lights
        if dry_run:
            print("  [DRY RUN] Would turn on outdoor front lights")
        else:
            requests.post(
                f"{ha_url}/api/services/light/turn_on",
                headers=headers,
                json={"entity_id": "light.outside_front_light_1"}
            )
            print("  ✓ Outdoor front lights turned on")
        automation_actions.append("Outdoor front lights turned on")

        section("Automation actions " + ("planned" if dry_run else "taken") + ":")
        for a in automation_actions:
            print(f"  → {a}")

    section("Sending to Mistral with full home context...")

    conditions_text = "\n".join(
        f"- [{c['severity']}] {c['type']}: {c['detail']}"
        for c in conditions
    )

    prompt = f"""You are a smart home security monitoring agent. It is currently 2:00am.

--- LIVE HOME STATE ---
{home_ctx_str}

--- SECURITY CONDITIONS DETECTED AT 2AM ---
{conditions_text}

--- ADDITIONAL CONTEXT ---
- Time: {DEMO_HOUR}:00am  |  Night hours: True
- Person home status: {person_home}
- Front door motion: {motion_front}
- Garage motion: {motion_garage}
- Garage 1: {garage1}  |  Garage 2: {garage2}
- Total home power: {total_watts}W
- AC Unit 1: {ac1_watts}W  |  AC Unit 2: {ac2_watts}W
- Oven: {oven_watts}W
- Lights on: {', '.join(lights_on) if lights_on else 'none'}
- Temperatures: Bedroom={temp_bedroom}°F | Living={temp_living}°F | Media={temp_media}°F

It is 2am. Cross-reference ALL conditions together.
Garage open + AC running + home empty at 2am is extremely serious.
Motion + garage open at night is a potential intrusion.
Oven running at 2am while home is empty is a fire safety emergency.
At 2am any HIGH condition warrants immediate SMS.

Choose ALERT_TYPE:
  INTRUSION  — motion detected at night with home empty or garage open
  ACCESS     — garage door left open at night
  ENERGY     — unexpected power draw while home appears empty
  SAFETY     — oven or hazardous device running while home empty at night
  COMBINED   — multiple conditions crossing security and energy

Choose SEVERITY:
  HIGH   — any condition at 2am with garage open, night motion, or oven while empty
  MEDIUM — occupied home with garage open, or power while away
  LOW    — minor concern only

SMS = YES if SEVERITY is HIGH.

--- RESPOND IN THIS EXACT FORMAT, NO OTHER TEXT ---
ALERT_TYPE: [INTRUSION | ACCESS | ENERGY | SAFETY | COMBINED]
SEVERITY: [HIGH | MEDIUM | LOW]
ALERT: [2-3 sentences — reference exact conditions, watts, temps, and 2am context. Be specific.]
REASONING: [1 sentence — cite the specific combination of factors that determined severity]
SMS: [YES or NO]
SMS_MESSAGE: [under 140 chars if YES, blank if NO]
"""

    raw    = call_mistral(prompt)
    result = parse_response(raw)

    # Code-level overrides
    if garage_open and not occupied:
        result["severity"] = "HIGH"
        result["send_sms"] = True

    if not occupied and oven_watts > 100:
        result["severity"] = "HIGH"
        result["send_sms"] = True

    result = apply_gate(result)

    section("Mistral response")
    field("ALERT_TYPE:", result["alert_type"] or "—")
    field("SEVERITY:",   result["severity"]   or "—")
    print()
    print(f"    {result['alert']}")
    print()
    field("REASONING:", "")
    print(f"    {result['reasoning']}")
    print()

    if result["send_sms"] and result["sms_message"]:
        field("SMS:", f"YES → {sms_to}")
        field("MESSAGE:", f'"{result["sms_message"]}"')
        section("Firing SMS...")
        ok = send_sms_demo(result["sms_message"], sms_to)
        if ok:
            print("  Delivered. Check your phone.")
    elif result["_suppressed"]:
        field("SMS:", f"BLOCKED by gate (severity={result['severity']})")
    else:
        field("SMS:", "NO")
    pause()
    
# ── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="EcoNest Inference Demo")
    parser.add_argument("--phone",   default=None, help="10-digit number e.g. 5307606746")
    parser.add_argument("--carrier", default=None, help="tmobile, verizon, cricket, boost, metro, sprint, uscellular, virgin")
    parser.add_argument("--gateway", default=None, help="Full gateway override e.g. 9725551234@mms.att.net")
    args = parser.parse_args()

    if args.gateway:
        sms_to = args.gateway
    elif args.phone and args.carrier:
        carrier_key = args.carrier.lower().replace("-", "").replace("&", "")
        gateway = CARRIERS.get(carrier_key)
        if not gateway:
            print(f"\nUnknown carrier '{args.carrier}'.")
            print(f"Supported: {', '.join(CARRIERS.keys())}")
            print(f"Or pass the gateway directly: --gateway NUMBER@your.gateway.com\n")
            sys.exit(1)
        sms_to = f"{args.phone}@{gateway}"
    else:
        sms_to = os.environ.get("SMS_TO")
        if not sms_to:
            print("No SMS destination. Pass --phone + --carrier, --gateway, or set SMS_TO in .env")
            sys.exit(1)

    print(f"\n{'═'*64}")
    print(f"  EcoNest Smart Home — Live Inference Demo")
    print(f"  {datetime.now().strftime('%A, %B %d %Y  %H:%M')}")
    print(f"  SMS destination : {sms_to}")
    print(f"  Model           : {MODEL} (running locally via Ollama)")
    print(f"{'═'*64}")
    print("""
  Six scenes — all using live data from the house right now:

    Scene 1 — Routine recommendation   energy advice, no SMS
    Scene 2 — Energy anomaly + 2am sim  classify now, then simulate 2am → SMS
    Scene 3 — Laundry energy tip        Mistral + live pricing + home context
    Scene 4 — Sprinkler morning check   soil + weather + pricing → SMS
    Scene 5 — Late night wind-down      TV off, lights dim, thermostat → sleep
    Scene 6 — Security check            garage + motion + power + 2am → SMS

  Every scene uses full cross-home context — thermostats, occupancy,
  breaker-level power, lights, washer/dryer, weather, soil moisture.
  No data is fabricated. Scene 2 uses whatever is anomalous right now.
""")
    input("  Press Enter to start...\n")

    scene_recommendation()
    input("\n  Press Enter for Scene 2...\n")

    scene_energy_alert(sms_to)    
    input("\n  Press Enter for Scene 3...\n")

    scene_laundry(sms_to)
    input("\n  Press Enter for Scene 4...\n")

    scene_sprinkler(sms_to)
    input("\n  Press Enter for Scene 5...\n")

    scene_winddown(sms_to, dry_run=False)
    input("\n  Press Enter for Scene 6...\n")

    scene_security(sms_to, dry_run=False)

    print(f"\n{'═'*64}")
    print("  Demo complete.")
    print(f"{'═'*64}\n")


if __name__ == "__main__":
    main()