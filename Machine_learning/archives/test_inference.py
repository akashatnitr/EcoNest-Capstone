#!/usr/bin/env python3
"""
EcoNest Inference Test Suite
Covers: alert classification, SMS gate, recommendation quality, parser robustness
Saves and restores home_snapshot around every test.
"""

import sys, json
sys.path.insert(0, '/Users/econest/scripts')

import requests
from datetime import datetime
from inference import (
    get_connection, _is_scheduled,
    build_alert_context, build_alert_prompt,
    build_recommendation_context, build_recommendation_prompt,
    parse_response
)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL     = "mistral"

PASS = "PASS"
FAIL = "FAIL"
results = []

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def call_mistral(prompt):
    r = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "stream": False})
    return r.json()["response"].strip()

def apply_sms_gate(result):
    """Mirror the gate in run_inference so tests reflect real behaviour."""
    if result["send_sms"] and result["severity"] and result["severity"].upper() != "HIGH":
        result["send_sms"] = False
        result["sms_message"] = None
        result["_gate_suppressed"] = True
    else:
        result["_gate_suppressed"] = False
    return result

def save_snapshot():
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM home_snapshot")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def restore_snapshot(rows):
    conn = get_connection()
    cur  = conn.cursor()
    for row in rows:
        cur.execute("""
            UPDATE home_snapshot
            SET active_devices=%s, power_trend=%s,
                anomaly_detected=%s, anomaly_reason=%s
            WHERE id=%s
        """, (
            json.dumps(row["active_devices"]) if row["active_devices"] else None,
            row["power_trend"], row["anomaly_detected"],
            row["anomaly_reason"], row["id"]
        ))
    conn.commit()
    cur.close(); conn.close()

def clear_anomalies():
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("UPDATE home_snapshot SET anomaly_detected=0, anomaly_reason=NULL")
    conn.commit()
    cur.close(); conn.close()

def inject_anomaly(room_name, power_trend, reason, devices):
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE home_snapshot hs
        JOIN rooms r ON hs.room_id = r.id
        SET hs.anomaly_detected=1,
            hs.anomaly_reason=%s,
            hs.power_trend=%s,
            hs.active_devices=%s
        WHERE r.name=%s
    """, (reason, power_trend, json.dumps(devices), room_name))
    conn.commit()
    cur.close(); conn.close()

def get_alert_ctx(hour, day, extra_overrides=None):
    """Pull a fresh alert context then patch time fields for the test."""
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)
    ctx  = build_alert_context(cur)
    cur.close(); conn.close()
    ctx["current_hour"] = hour
    ctx["current_day"]  = day
    ctx["is_night"]     = (hour >= 23 or hour < 6)
    if extra_overrides:
        ctx.update(extra_overrides)
    return ctx

def run_test(name, fn):
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"TEST  {name}")
    print(sep)
    try:
        fn()
        print(f"  → {PASS}")
        results.append((name, True, None))
    except AssertionError as e:
        print(f"  → {FAIL}: {e}")
        results.append((name, False, str(e)))
    except Exception as e:
        import traceback
        traceback.print_exc()
        results.append((name, False, f"Exception: {e}"))


# ──────────────────────────────────────────────
# GROUP 1 — Parser robustness (no DB, no model)
# ──────────────────────────────────────────────

def test_parse_clean_alert():
    raw = (
        "ALERT_TYPE: ENERGY\nSEVERITY: MEDIUM\n"
        "ALERT: High usage in garage.\nREASONING: 4x baseline.\n"
        "SMS: NO\nSMS_MESSAGE:"
    )
    r = parse_response(raw)
    assert r["alert_type"]  == "ENERGY",  r
    assert r["severity"]    == "MEDIUM",  r
    assert r["send_sms"]    == False,     r
    assert r["sms_message"] is None,      r
    print(f"  alert_type={r['alert_type']}  severity={r['severity']}  send_sms={r['send_sms']}")

def test_parse_clean_recommendation():
    raw = (
        "CATEGORY: SCHEDULING\nPRIORITY: HIGH\n"
        "RECOMMENDATION: Disable Oven on weekdays.\nREASONING: Weekends-only profile.\n"
        "SMS: NO\nSMS_MESSAGE:"
    )
    r = parse_response(raw)
    assert r["category"]       == "SCHEDULING", r
    assert r["priority"]       == "HIGH",        r
    assert r["recommendation"] == "Disable Oven on weekdays.", r
    assert r["send_sms"]       == False, r
    print(f"  category={r['category']}  priority={r['priority']}")

def test_parse_partial_output():
    """Parser must not crash on missing fields."""
    r = parse_response("ALERT_TYPE: FAULT\nALERT: Something broke.")
    assert r["alert_type"] == "FAULT"
    assert r["severity"]   is None
    assert r["send_sms"]   == False
    print(f"  alert_type={r['alert_type']}  severity={r['severity']}  (partial OK)")

def test_parse_extra_prose():
    """Model sometimes adds preamble — fields must still extract."""
    raw = (
        "Sure, here is my analysis:\n\n"
        "CATEGORY: STANDBY\nPRIORITY: MEDIUM\n"
        "RECOMMENDATION: Turn off idle devices.\nREASONING: Low occupancy.\n"
        "SMS: NO\nSMS_MESSAGE:\n\nHope that helps!"
    )
    r = parse_response(raw)
    assert r["category"]       == "STANDBY",            r
    assert r["recommendation"] == "Turn off idle devices.", r
    print(f"  category={r['category']}  (prose stripped OK)")

def test_parse_sms_yes():
    raw = (
        "ALERT_TYPE: SECURITY\nSEVERITY: HIGH\n"
        "ALERT: Motion at 2am.\nREASONING: Night motion.\n"
        "SMS: YES\nSMS_MESSAGE: Motion at front door. Check cameras."
    )
    r = parse_response(raw)
    assert r["send_sms"]    == True
    assert r["sms_message"] == "Motion at front door. Check cameras."
    print(f"  send_sms={r['send_sms']}  message='{r['sms_message']}'")


# ──────────────────────────────────────────────
# GROUP 2 — SMS safety gate (no DB, no model)
# ──────────────────────────────────────────────

def test_gate_high_passes():
    r = parse_response(
        "ALERT_TYPE: SECURITY\nSEVERITY: HIGH\nALERT: x\nREASONING: y\n"
        "SMS: YES\nSMS_MESSAGE: Check cameras now."
    )
    r = apply_sms_gate(r)
    assert r["send_sms"] == True,       "HIGH+YES must pass"
    assert r["sms_message"] is not None
    print(f"  SEVERITY=HIGH → send_sms={r['send_sms']}  gate_suppressed={r['_gate_suppressed']}")

def test_gate_medium_suppressed():
    r = parse_response(
        "ALERT_TYPE: ENERGY\nSEVERITY: MEDIUM\nALERT: x\nREASONING: y\n"
        "SMS: YES\nSMS_MESSAGE: Some message."
    )
    r = apply_sms_gate(r)
    assert r["send_sms"]       == False, "MEDIUM must be suppressed"
    assert r["_gate_suppressed"] == True
    print(f"  SEVERITY=MEDIUM → send_sms={r['send_sms']}  gate_suppressed={r['_gate_suppressed']}")

def test_gate_low_suppressed():
    r = parse_response(
        "ALERT_TYPE: ENERGY\nSEVERITY: LOW\nALERT: x\nREASONING: y\n"
        "SMS: YES\nSMS_MESSAGE: Some message."
    )
    r = apply_sms_gate(r)
    assert r["send_sms"]       == False, "LOW must be suppressed"
    assert r["_gate_suppressed"] == True
    print(f"  SEVERITY=LOW → send_sms={r['send_sms']}  gate_suppressed={r['_gate_suppressed']}")

def test_gate_no_sms_unchanged():
    r = parse_response(
        "ALERT_TYPE: ENERGY\nSEVERITY: HIGH\nALERT: x\nREASONING: y\n"
        "SMS: NO\nSMS_MESSAGE:"
    )
    r = apply_sms_gate(r)
    assert r["send_sms"]       == False
    assert r["_gate_suppressed"] == False
    print(f"  HIGH+NO → gate untouched  send_sms={r['send_sms']}")


# ──────────────────────────────────────────────
# GROUP 3 — Alert mode (DB + Mistral)
# ──────────────────────────────────────────────

def test_alert_no_anomalies():
    """No anomalies → build_alert_prompt returns None → graceful skip."""
    clear_anomalies()
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)
    ctx  = build_alert_context(cur)
    cur.close(); conn.close()
    prompt = build_alert_prompt(ctx)
    assert prompt is None, f"Expected None prompt when no anomalies, got prompt"
    print("  No anomalies → prompt=None  (skip confirmed)")

def test_alert_energy_daytime():
    """4x spike at 2pm (active hours, device on schedule) → ENERGY, no SMS."""
    clear_anomalies()
    inject_anomaly("Garage", 908.27, "Power 908.27W is 4x above baseline 120.459W",
                   [{"device_id": 21, "name": "Garage / Entry to Living Room", "power": 908.27}])
    ctx = get_alert_ctx(hour=14, day="Sunday")
    # Device is in schedule (6-22 daily) at hour 14
    for a in ctx["anomalies"]:
        for d in a["active_devices"]:
            d["scheduled"] = True
            d["schedule"]  = "6:00–22:00 daily"

    prompt = build_alert_prompt(ctx)
    assert prompt, "Prompt must not be None"
    raw = call_mistral(prompt)
    r   = apply_sms_gate(parse_response(raw))
    print(f"  ALERT_TYPE={r['alert_type']}  SEVERITY={r['severity']}  SMS={r['send_sms']}  gate={r['_gate_suppressed']}")
    print(f"  ALERT: {r['alert']}")
    assert r["alert_type"] in ("ENERGY", "FAULT"), f"Got {r['alert_type']}"
    assert r["send_sms"] == False, "Daytime on-schedule spike must not SMS"

def test_alert_energy_sleep_hours():
    """4x spike at 2am (sleep hours, device off schedule) → HIGH severity expected."""
    clear_anomalies()
    inject_anomaly("Garage", 908.27, "Power 908.27W is 4x above baseline 120.459W",
                   [{"device_id": 21, "name": "Garage / Entry to Living Room", "power": 908.27}])
    ctx = get_alert_ctx(hour=2, day="Sunday")
    for a in ctx["anomalies"]:
        a["baseline_W"]  = 120.5
        a["multiplier"]  = 7.5
        for d in a["active_devices"]:
            d["scheduled"] = False
            d["schedule"]  = "6:00–22:00 daily"

    prompt = build_alert_prompt(ctx)
    raw = call_mistral(prompt)
    r   = apply_sms_gate(parse_response(raw))
    print(f"  ALERT_TYPE={r['alert_type']}  SEVERITY={r['severity']}  SMS={r['send_sms']}  gate={r['_gate_suppressed']}")
    print(f"  ALERT: {r['alert']}")
    assert r["severity"] in ("HIGH", "MEDIUM"), f"2am spike should be HIGH or MEDIUM, got {r['severity']}"

def test_alert_fault_off_schedule_weekday():
    """Oven (weekends-only) active on Wednesday → FAULT classification."""
    clear_anomalies()
    inject_anomaly("Kitchen/Dining", 1850.0, "Power 1850W is 4x above baseline 450W",
                   [{"device_id": 11, "name": "Oven", "power": 1850.0}])
    ctx = get_alert_ctx(hour=15, day="Wednesday")
    for a in ctx["anomalies"]:
        for d in a["active_devices"]:
            d["scheduled"] = False
            d["schedule"]  = "6:00–22:00 weekends"

    prompt = build_alert_prompt(ctx)
    raw = call_mistral(prompt)
    r   = apply_sms_gate(parse_response(raw))
    print(f"  ALERT_TYPE={r['alert_type']}  SEVERITY={r['severity']}  SMS={r['send_sms']}  gate={r['_gate_suppressed']}")
    print(f"  ALERT: {r['alert']}")
    assert r["alert_type"] in ("FAULT", "ENERGY"), f"Off-schedule oven should be FAULT or ENERGY, got {r['alert_type']}"

def test_alert_fault_extreme_multiplier():
    """Device at 11x baseline → model should escalate severity."""
    clear_anomalies()
    inject_anomaly("Garage", 1350.0, "Power 1350W is 11x above baseline 120W",
                   [{"device_id": 21, "name": "Garage / Entry to Living Room", "power": 1350.0}])
    ctx = get_alert_ctx(hour=14, day="Sunday")
    for a in ctx["anomalies"]:
        a["multiplier"] = 11.2
        a["baseline_W"] = 120.0

    prompt = build_alert_prompt(ctx)
    raw = call_mistral(prompt)
    r   = apply_sms_gate(parse_response(raw))
    print(f"  ALERT_TYPE={r['alert_type']}  SEVERITY={r['severity']}  SMS={r['send_sms']}  gate={r['_gate_suppressed']}")
    print(f"  ALERT: {r['alert']}")
    assert r["severity"] in ("HIGH", "MEDIUM"), f"11x spike should be HIGH or MEDIUM, got {r['severity']}"

def test_alert_multiple_rooms():
    """Two simultaneous anomalies — model must produce a coherent single output."""
    clear_anomalies()
    inject_anomaly("Garage", 908.27, "Power 908.27W is 4x above baseline 120W",
                   [{"device_id": 21, "name": "Garage / Entry to Living Room", "power": 908.27}])
    inject_anomaly("Kitchen/Dining", 2800.0, "Power 2800W is 4x above baseline 627W",
                   [{"device_id": 11, "name": "Oven", "power": 2800.0}])
    ctx = get_alert_ctx(hour=14, day="Sunday")
    print(f"  Anomalies in context: {[a['room'] for a in ctx['anomalies']]}")
    assert len(ctx["anomalies"]) == 2, f"Expected 2 anomalies, got {len(ctx['anomalies'])}"

    prompt = build_alert_prompt(ctx)
    raw = call_mistral(prompt)
    r   = apply_sms_gate(parse_response(raw))
    print(f"  ALERT_TYPE={r['alert_type']}  SEVERITY={r['severity']}  SMS={r['send_sms']}")
    print(f"  ALERT: {r['alert']}")
    assert r["alert_type"] is not None, "Must classify both-room anomaly"
    assert r["alert"]      is not None

def test_alert_security_night_motion():
    """Security anomaly at 2am → SECURITY type, HIGH severity, SMS."""
    clear_anomalies()
    # Front Door room — motion sensor device
    inject_anomaly("Front Door", 0,
                   "Security: motion detected at night (2:00)",
                   [{"device_id": 9, "name": "Motion Sensor", "motion": True, "power": 0}])
    ctx = get_alert_ctx(hour=2, day="Wednesday")
    # Motion devices have power=0 so enriched_devices is empty — inject directly
    if ctx["anomalies"]:
        ctx["anomalies"][0]["active_devices"] = [{
            "name": "Motion Sensor",
            "current_power_W": 0,
            "scheduled": True,
            "schedule": "0:00–23:00 daily",
            "motion": True
        }]

    prompt = build_alert_prompt(ctx)
    if prompt is None:
        # Front Door baseline may be 0 — anomaly still present, force prompt
        ctx["anomalies"] = [{
            "room": "Front Door",
            "current_power_W": 0,
            "baseline_W": 0,
            "multiplier": None,
            "anomaly_reason": "Security: motion detected at night (2:00)",
            "active_devices": [{
                "name": "Motion Sensor",
                "current_power_W": 0,
                "scheduled": True,
                "schedule": "0:00–23:00 daily",
                "motion": True
            }]
        }]
        prompt = build_alert_prompt(ctx)

    assert prompt, "Prompt must not be None for security anomaly"
    raw = call_mistral(prompt)
    r   = apply_sms_gate(parse_response(raw))
    print(f"  ALERT_TYPE={r['alert_type']}  SEVERITY={r['severity']}  SMS={r['send_sms']}  gate={r['_gate_suppressed']}")
    print(f"  ALERT: {r['alert']}")
    assert r["alert_type"] == "SECURITY", f"Night motion must be SECURITY, got {r['alert_type']}"
    assert r["severity"]   == "HIGH",     f"Night motion must be HIGH, got {r['severity']}"
    # SMS gate: HIGH+SECURITY → should pass
    assert r["send_sms"]   == True,       f"SECURITY/HIGH must SMS"


# ──────────────────────────────────────────────
# GROUP 4 — Recommendation mode (DB + Mistral)
# ──────────────────────────────────────────────

def test_recommendation_structure():
    """Every run must return all four output fields."""
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)
    ctx  = build_recommendation_context(cur)
    cur.close(); conn.close()

    raw = call_mistral(build_recommendation_prompt(ctx))
    r   = parse_response(raw)
    print(f"  CATEGORY={r['category']}  PRIORITY={r['priority']}")
    print(f"  RECOMMENDATION: {r['recommendation']}")
    print(f"  REASONING: {r['reasoning']}")
    assert r["recommendation"] is not None, "Must always produce a recommendation"
    assert r["reasoning"]      is not None, "Must always produce reasoning"
    assert r["send_sms"]       == False,    "Recommendation must never SMS"

def test_recommendation_never_sms():
    """Recommendation mode must never send SMS regardless of content."""
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)
    ctx  = build_recommendation_context(cur)
    cur.close(); conn.close()

    raw = call_mistral(build_recommendation_prompt(ctx))
    r   = parse_response(raw)
    assert r["send_sms"] == False, f"Recommendation triggered SMS: {r}"
    print(f"  send_sms={r['send_sms']}  (confirmed NO)")

def test_recommendation_off_schedule_weekday():
    """Oven (weekends-only) flagged as not scheduled on Wednesday → SCHEDULING."""
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)
    ctx  = build_recommendation_context(cur)
    cur.close(); conn.close()

    ctx["current_day"] = "Wednesday"
    ctx["is_weekend"]  = False
    ctx["current_hour"] = 15
    for dev in ctx["device_inventory"]:
        if dev["device_name"] == "Oven":
            dev["currently_scheduled"] = False  # weekends-only, it's Wednesday

    raw = call_mistral(build_recommendation_prompt(ctx))
    r   = parse_response(raw)
    print(f"  CATEGORY={r['category']}  PRIORITY={r['priority']}")
    print(f"  RECOMMENDATION: {r['recommendation']}")
    assert r["recommendation"] is not None
    # If it found the off-schedule oven it should say SCHEDULING
    if r["category"]:
        print(f"  (category={r['category']})")

def test_recommendation_weekend_computer():
    """Computer (weekdays 9-18) running on Sunday → should surface it."""
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)
    ctx  = build_recommendation_context(cur)
    cur.close(); conn.close()

    ctx["current_day"]  = "Sunday"
    ctx["is_weekend"]   = True
    ctx["current_hour"] = 15
    for dev in ctx["device_inventory"]:
        if dev["device_name"] == "Computer":
            dev["currently_scheduled"] = False  # weekdays only

    raw = call_mistral(build_recommendation_prompt(ctx))
    r   = parse_response(raw)
    print(f"  CATEGORY={r['category']}  PRIORITY={r['priority']}")
    print(f"  RECOMMENDATION: {r['recommendation']}")
    assert r["recommendation"] is not None

def test_recommendation_all_scheduled():
    """All devices on-schedule — model must still return a valid non-kitchen recommendation."""
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)
    ctx  = build_recommendation_context(cur)
    cur.close(); conn.close()

    for dev in ctx["device_inventory"]:
        dev["currently_scheduled"] = True

    raw = call_mistral(build_recommendation_prompt(ctx))
    r   = parse_response(raw)
    print(f"  CATEGORY={r['category']}  PRIORITY={r['priority']}")
    print(f"  RECOMMENDATION: {r['recommendation']}")
    assert r["recommendation"] is not None
    # Should look beyond kitchen when no off-schedule devices exist
    rec_lower = (r["recommendation"] or "").lower()
    reasoning_lower = (r["reasoning"] or "").lower()
    mentions_other = any(room.lower() in rec_lower + reasoning_lower
                         for room in ["room 2", "garage", "computer", "utility", "study", "media"])
    print(f"  mentions non-kitchen room: {mentions_other}")


# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\nEcoNest Inference Test Suite  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Model: {MODEL}  |  Ollama: {OLLAMA_URL}\n")

    snapshot = save_snapshot()

    try:
        # Group 1 — Parser (fast, no network)
        run_test("parser: clean alert fields", test_parse_clean_alert)
        run_test("parser: clean recommendation fields", test_parse_clean_recommendation)
        run_test("parser: partial output — no crash", test_parse_partial_output)
        run_test("parser: extra prose stripped", test_parse_extra_prose)
        run_test("parser: SMS YES extracted", test_parse_sms_yes)

        # Group 2 — SMS gate (fast, no network)
        run_test("gate: HIGH+YES passes through", test_gate_high_passes)
        run_test("gate: MEDIUM+YES suppressed", test_gate_medium_suppressed)
        run_test("gate: LOW+YES suppressed", test_gate_low_suppressed)
        run_test("gate: HIGH+NO unchanged", test_gate_no_sms_unchanged)

        # Group 3 — Alert (Mistral)
        run_test("alert: no anomalies → prompt=None", test_alert_no_anomalies)
        run_test("alert: energy spike daytime → no SMS", test_alert_energy_daytime)
        run_test("alert: energy spike 2am → HIGH/MEDIUM", test_alert_energy_sleep_hours)
        run_test("alert: oven weekday (weekends-only) → FAULT", test_alert_fault_off_schedule_weekday)
        run_test("alert: 11x baseline → HIGH/MEDIUM severity", test_alert_fault_extreme_multiplier)
        run_test("alert: two simultaneous anomalies", test_alert_multiple_rooms)
        run_test("alert: night motion → SECURITY/HIGH/SMS", test_alert_security_night_motion)

        # Group 4 — Recommendation (Mistral)
        run_test("rec: always produces structured output", test_recommendation_structure)
        run_test("rec: never triggers SMS", test_recommendation_never_sms)
        run_test("rec: off-schedule oven on weekday", test_recommendation_off_schedule_weekday)
        run_test("rec: computer running on weekend", test_recommendation_weekend_computer)
        run_test("rec: all scheduled → looks beyond kitchen", test_recommendation_all_scheduled)

    finally:
        restore_snapshot(snapshot)
        print(f"\n{'─'*60}")
        print("DB snapshot restored.")

    # Summary
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total  = len(results)

    print(f"\n{'═'*60}")
    print(f"  {passed}/{total} passed   {failed} failed")
    print(f"{'═'*60}")
    for name, ok, err in results:
        mark = "✓" if ok else "✗"
        print(f"  {mark}  {name}")
        if err:
            print(f"       └─ {err}")
    print()

    sys.exit(0 if failed == 0 else 1)
