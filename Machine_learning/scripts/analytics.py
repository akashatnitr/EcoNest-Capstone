import mysql.connector
import json
from datetime import datetime
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

def get_connection():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", "econest"),
        database=os.environ.get("DB_NAME", "econest"),
        port=int(os.environ.get("DB_PORT", 3306)),
        ssl_disabled=False
    )
    
def compute_analytics():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    current_hour = datetime.now().hour

    #Get all rooms that have active devices
    cursor.execute("""
        SELECT DISTINCT room_id FROM devices WHERE is_active = TRUE
    """)
    rooms = cursor.fetchall()

    for room in rooms:
        room_id = room["room_id"]
        #Average power this hour over the last 7 days
        cursor.execute("""
            SELECT AVG(JSON_EXTRACT(sr.data, '$.power')) as avg_power
            FROM sensor_readings sr
            JOIN devices d ON sr.device_id = d.id
            WHERE d.room_id = %s
              AND d.device_type = 'energy'
              AND HOUR(sr.timestamp) = %s
              AND sr.timestamp >= NOW() - INTERVAL 7 DAY
        """, (room_id, current_hour))
        power_result = cursor.fetchone()
        avg_power = float(power_result["avg_power"]) if power_result["avg_power"] else 0.0

        # ----------------------------------------------------------
        # 2. Total kWh for this room over the last 7 days
        #    avg watts * 24 hours * 7 days / 1000 = kWh
        # ----------------------------------------------------------
        cursor.execute("""
            SELECT AVG(JSON_EXTRACT(sr.data, '$.power')) as avg_watts
            FROM sensor_readings sr
            JOIN devices d ON sr.device_id = d.id
            WHERE d.room_id = %s
              AND d.device_type = 'energy'
              AND sr.timestamp >= NOW() - INTERVAL 7 DAY
        """, (room_id,))
        kwh_result = cursor.fetchone()
        avg_watts = float(kwh_result["avg_watts"]) if kwh_result["avg_watts"] else 0.0
        total_kwh = round(avg_watts * 24 * 7 / 1000, 4)

        #Weekly pattern — avg power per hour of day
        cursor.execute("""
            SELECT HOUR(sr.timestamp) as hour,
                   AVG(JSON_EXTRACT(sr.data, '$.power')) as avg_power
            FROM sensor_readings sr
            JOIN devices d ON sr.device_id = d.id
            WHERE d.room_id = %s
              AND d.device_type = 'energy'
              AND sr.timestamp >= NOW() - INTERVAL 7 DAY
            GROUP BY HOUR(sr.timestamp)
            ORDER BY hour
        """, (room_id,))
        hourly_rows = cursor.fetchall()
        weekly_pattern = {
            str(row["hour"]): round(float(row["avg_power"]), 2)
            for row in hourly_rows
        }

        #Room 7 (Front Door) — add motion stats to weekly_pattern
        if room_id == 7:
            cursor.execute("""
                SELECT HOUR(sr.timestamp) as hour,
                       SUM(JSON_EXTRACT(sr.data, '$.motion') = true) as motion_count,
                       COUNT(*) as total_readings
                FROM sensor_readings sr
                JOIN devices d ON sr.device_id = d.id
                WHERE d.room_id = 7
                  AND d.device_type = 'motion'
                  AND sr.timestamp >= NOW() - INTERVAL 7 DAY
                GROUP BY HOUR(sr.timestamp)
                ORDER BY hour
            """)
            motion_rows = cursor.fetchall()
            weekly_pattern["motion_events_per_hour"] = {
                str(row["hour"]): round(
                    float(row["motion_count"]) / float(row["total_readings"]), 2
                ) if row["total_readings"] > 0 else 0.0
                for row in motion_rows
            }

        #Room 1 (Kitchen) — add sound stats to weekly_pattern
        if room_id == 1:
            cursor.execute("""
                SELECT HOUR(sr.timestamp) as hour,
                       AVG(JSON_EXTRACT(sr.data, '$.sound_level')) as avg_sound
                FROM sensor_readings sr
                JOIN devices d ON sr.device_id = d.id
                WHERE d.room_id = 1
                  AND d.device_type = 'sound'
                  AND sr.timestamp >= NOW() - INTERVAL 7 DAY
                GROUP BY HOUR(sr.timestamp)
                ORDER BY hour
            """)
            sound_rows = cursor.fetchall()
            weekly_pattern["avg_sound_level"] = {
                str(row["hour"]): round(float(row["avg_sound"]), 2)
                for row in sound_rows
                if row["avg_sound"] is not None
            }

        #Insert into home_analytics
        cursor.execute("""
            INSERT INTO home_analytics 
                (room_id, hour_of_day, avg_power_this_hour, total_kwh, weekly_pattern, computed_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                avg_power_this_hour = VALUES(avg_power_this_hour),
                total_kwh = VALUES(total_kwh),
                weekly_pattern = VALUES(weekly_pattern),
                computed_at = NOW()
        """, (room_id, current_hour, avg_power, total_kwh, json.dumps(weekly_pattern)))

    conn.commit()
    print(f"[{datetime.now()}] Analytics computed for {len(rooms)} room(s), hour={current_hour}")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    compute_analytics()