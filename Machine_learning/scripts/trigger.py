import mysql.connector
import json
import time
import subprocess
from datetime import datetime
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

DRY_RUN = False  # set to False when ready to write to DB

NIGHT_START = 23  # 11pm
NIGHT_END = 6     # 6am

def get_connection():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", "econest"),
        database=os.environ.get("DB_NAME", "econest"),
        port=int(os.environ.get("DB_PORT", 3306)),
        ssl_disabled=False
    )

def get_latest_readings(cursor, room_id, minutes=10):
    """Pull most recent sensor readings for a room"""
    cursor.execute("""
        SELECT sr.device_id, d.device_type, d.name, sr.data, sr.timestamp
        FROM sensor_readings sr
        JOIN devices d ON sr.device_id = d.id
        WHERE d.room_id = %s
          AND sr.timestamp >= NOW() - INTERVAL %s MINUTE
        ORDER BY sr.timestamp DESC
    """, (room_id, minutes))
    return cursor.fetchall()

def get_device_profiles(cursor, room_id):
    """Pull device profiles for a room"""
    cursor.execute("""
        SELECT dp.device_id, dp.device_name, dp.active_hours_start,
               dp.active_hours_end, dp.active_days
        FROM device_profiles dp
        JOIN devices d ON dp.device_id = d.id
        WHERE d.room_id = %s
    """, (room_id,))
    return {row["device_id"]: row for row in cursor.fetchall()}

def get_home_analytics(cursor, room_id, hour):
    """Pull baseline analytics for this room at this hour"""
    cursor.execute("""
        SELECT avg_power_this_hour, total_kwh, weekly_pattern
        FROM home_analytics
        WHERE room_id = %s AND hour_of_day = %s
    """, (room_id, hour))
    return cursor.fetchone()

def check_motion_and_sound(cursor, current_hour):
    """
    Security anomaly checks:
    1. Motion + sound spike at any hour
    2. Motion alone at night (11pm - 6am)
    """
    # Check recent motion
    cursor.execute("""
        SELECT data FROM sensor_readings
        WHERE device_id = 9
          AND timestamp >= NOW() - INTERVAL 2 MINUTE
          AND JSON_EXTRACT(data, '$.motion') = true
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    motion = cursor.fetchone()
    motion_detected = motion is not None

    # Check recent sound level vs baseline
    cursor.execute("""
        SELECT JSON_EXTRACT(data, '$.sound_level') as sound_level
        FROM sensor_readings
        WHERE device_id = 10
          AND timestamp >= NOW() - INTERVAL 2 MINUTE
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    sound_result = cursor.fetchone()
    current_sound = float(sound_result["sound_level"]) if sound_result and sound_result["sound_level"] else None

    # Get sound baseline from home_analytics weekly_pattern for room 1
    cursor.execute("""
        SELECT weekly_pattern FROM home_analytics
        WHERE room_id = 1 AND hour_of_day = %s
    """, (current_hour,))
    analytics = cursor.fetchone()
    sound_baseline = None
    if analytics and analytics["weekly_pattern"]:
        pattern = json.loads(analytics["weekly_pattern"]) if isinstance(analytics["weekly_pattern"], str) else analytics["weekly_pattern"]
        avg_sound = pattern.get("avg_sound_level", {})
        sound_baseline = avg_sound.get(str(current_hour), None)

    sound_spike = (
        current_sound is not None and
        sound_baseline is not None and
        current_sound > sound_baseline * 1.5
    )

    # Check 1: Motion + sound spike at any hour
    if motion_detected and sound_spike:
        return True, f"Security: motion detected with sound spike ({current_sound:.1f}dB vs baseline {sound_baseline:.1f}dB)"

    # Check 2: Motion alone at night
    is_night = current_hour >= NIGHT_START or current_hour < NIGHT_END
    if motion_detected and is_night:
        return True, f"Security: motion detected at night ({current_hour}:00)"

    return False, None

def compute_signals(readings, analytics, current_hour):
    """
    Analyze readings and return derived signals.
    Only flags power-based anomalies here.
    """
    power_readings = []
    active_devices = []
    anomaly_detected = False
    anomaly_reason = None

    seen_devices = set()  # deduplicate — only take most recent reading per device

    for row in readings:
        device_id = row["device_id"]
        device_type = row["device_type"]
        device_name = row["name"]
        data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])

        if device_id in seen_devices:
            continue
        seen_devices.add(device_id)

        # --- Energy devices ---
        if device_type == "energy":
            power = data.get("power", 0)
            if power and power > 0:
                power_readings.append(power)
                active_devices.append({
                    "device_id": device_id,
                    "name": device_name,
                    "power": round(power, 2)
                })

        # --- Motion sensor ---
        elif device_type == "motion":
            motion = data.get("motion", False)
            if motion:
                active_devices.append({
                    "device_id": device_id,
                    "name": device_name,
                    "motion": True
                })

        # --- Sound sensor ---
        elif device_type == "sound":
            sound_level = data.get("sound_level")
            if sound_level is not None:
                active_devices.append({
                    "device_id": device_id,
                    "name": device_name,
                    "sound_level": round(sound_level, 2)
                })

    # Average power trend for this window
    power_trend = round(sum(power_readings) / len(power_readings), 2) if power_readings else 0.0

    # Check power vs baseline
    # Rules:
    #   1. Baseline must be > 20W to avoid flagging low-draw rooms
    #   2. Current power must be > 100W minimum to be meaningful
    #   3. Must be 4x above baseline
    if analytics and analytics["avg_power_this_hour"]:
        baseline = float(analytics["avg_power_this_hour"])
        if (
            baseline > 20 and           # ignore rooms with tiny baselines
            power_trend > 100 and       # minimum meaningful wattage
            power_trend > baseline * 4  # 4x threshold
        ):
            anomaly_detected = True
            anomaly_reason = f"Power {power_trend}W is 4x above baseline {baseline}W"

    return {
        "power_trend": power_trend,
        "active_devices": active_devices,
        "anomaly_detected": anomaly_detected,
        "anomaly_reason": anomaly_reason
    }

def update_snapshot(cursor, conn, room_id, signals):
    """Upsert home_snapshot for this room"""
    cursor.execute("SELECT id FROM home_snapshot WHERE room_id = %s", (room_id,))
    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE home_snapshot
            SET active_devices = %s,
                power_trend = %s,
                anomaly_detected = %s,
                anomaly_reason = %s,
                updated_at = NOW()
            WHERE room_id = %s
        """, (
            json.dumps(signals["active_devices"]),
            signals["power_trend"],
            signals["anomaly_detected"],
            signals["anomaly_reason"],
            room_id
        ))
    else:
        cursor.execute("""
            INSERT INTO home_snapshot
                (room_id, active_devices, power_trend, anomaly_detected, anomaly_reason)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            room_id,
            json.dumps(signals["active_devices"]),
            signals["power_trend"],
            signals["anomaly_detected"],
            signals["anomaly_reason"]
        ))
    conn.commit()

def run():
    print(f"[{datetime.now()}] Trigger check started — DRY_RUN={DRY_RUN}")

    while True:
        try:
            conn = get_connection()
            cursor = conn.cursor(dictionary=True)

            current_hour = datetime.now().hour

            # Check security anomaly once per cycle (home-level)
            security_anomaly, security_reason = check_motion_and_sound(cursor, current_hour)
            if security_anomaly:
                print(f"[{datetime.now()}] SECURITY ANOMALY: {security_reason}")
                if not DRY_RUN:
                    subprocess.Popen([
                        "/opt/homebrew/bin/python3",
                        "/Users/econest/scripts/inference.py",
                        "--mode", "alert"
                    ])

            # Get all rooms with active devices
            cursor.execute("""
                SELECT DISTINCT room_id FROM devices WHERE is_active = TRUE
            """)
            rooms = cursor.fetchall()

            for room in rooms:
                room_id = room["room_id"]

                readings = get_latest_readings(cursor, room_id, minutes=10)
                if not readings:
                    continue

                analytics = get_home_analytics(cursor, room_id, current_hour)
                signals = compute_signals(readings, analytics, current_hour)

                if signals["anomaly_detected"]:
                    print(f"[{datetime.now()}] ANOMALY room_id={room_id}: {signals['anomaly_reason']}")
                    if not DRY_RUN:
                        subprocess.Popen([
                            "/opt/homebrew/bin/python3",
                            "/Users/econest/scripts/inference.py",
                            "--mode", "alert"
                        ])
                else:
                    print(f"[{datetime.now()}] room_id={room_id} OK — power={signals['power_trend']}W")

                if not DRY_RUN:
                    update_snapshot(cursor, conn, room_id, signals)

            cursor.close()
            conn.close()

        except Exception as e:
            print(f"[{datetime.now()}] ERROR: {e}")

        time.sleep(30)

if __name__ == "__main__":
    run()