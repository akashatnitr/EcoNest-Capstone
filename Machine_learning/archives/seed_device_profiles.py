import mysql.connector
import json

conn = mysql.connector.connect(
    host="localhost",
    user="root",
    password="econest",  # add your local MySQL root password here
    database="econest"
)

cursor = conn.cursor()

profiles = [
    # Kitchen devices
    {
        "device_id": None,
        "room": "Kitchen",
        "connected_device_name": "Dishwasher",
        "active_hours": json.dumps([{"start": 21, "end": 23}]),
        "active_days": "daily"
    },
    {
        "device_id": None,
        "room": "Kitchen",
        "connected_device_name": "Microwave",
        "active_hours": json.dumps([{"start": 7, "end": 9}, {"start": 16, "end": 19}]),
        "active_days": "daily"
    },
    {
        "device_id": None,
        "room": "Kitchen",
        "connected_device_name": "Oven",
        "active_hours": json.dumps([]),  # random hours
        "active_days": "weekends"
    },
    # Master Bedroom
    {
        "device_id": None,
        "room": "Master Bedroom",
        "connected_device_name": "Master Bedroom TV",
        "active_hours": json.dumps([{"start": 19, "end": 22}]),
        "active_days": "daily"
    },
    # Living Room
    {
        "device_id": None,
        "room": "Living Room",
        "connected_device_name": "Xbox",
        "active_hours": json.dumps([{"start": 16, "end": 22}]),
        "active_days": "weekdays"
    },
    {
        "device_id": None,
        "room": "Living Room",
        "connected_device_name": "Xbox",
        "active_hours": json.dumps([{"start": 0, "end": 23}]),  # all day
        "active_days": "weekends"
    },
    {
        "device_id": None,
        "room": "Living Room",
        "connected_device_name": "Living Room TV",
        "active_hours": json.dumps([{"start": 7, "end": 9}, {"start": 16, "end": 18}]),
        "active_days": "daily"
    },
    # Computer Room
    {
        "device_id": None,
        "room": "Computer Room",
        "connected_device_name": "Computer",
        "active_hours": json.dumps([{"start": 9, "end": 18}]),
        "active_days": "daily"
    },
    # Laundry
    {
        "device_id": None,
        "room": "Laundry",
        "connected_device_name": "Washing Machine",
        "active_hours": json.dumps([]),  # random hours
        "active_days": "weekends"
    },
    {
        "device_id": None,
        "room": "Laundry",
        "connected_device_name": "Dryer",
        "active_hours": json.dumps([]),  # random hours
        "active_days": "weekends"
    },
    # Motion Sensors
    {
        "device_id": None,
        "room": "Front Door",
        "connected_device_name": "Blink Front Door",
        "active_hours": json.dumps([{"start": 0, "end": 23}]),
        "active_days": "daily"
    },
    {
        "device_id": None,
        "room": "Back Door",
        "connected_device_name": "Blink Back Door",
        "active_hours": json.dumps([{"start": 0, "end": 23}]),
        "active_days": "daily"
    },
    {
        "device_id": None,
        "room": "Garage",
        "connected_device_name": "Blink Garage Camera",
        "active_hours": json.dumps([{"start": 0, "end": 23}]),
        "active_days": "daily"
    },
    # Sound Sensor
    {
        "device_id": None,
        "room": "Bedroom",
        "connected_device_name": "Bedroom Sound Sensor",
        "active_hours": json.dumps([{"start": 0, "end": 23}]),
        "active_days": "daily"
    }
]

insert_query = """
    INSERT INTO device_profiles (device_id, room, connected_device_name, active_hours, active_days)
    VALUES (%s, %s, %s, %s, %s)
"""

for p in profiles:
    cursor.execute(insert_query, (
        p["device_id"],
        p["room"],
        p["connected_device_name"],
        p["active_hours"],
        p["active_days"]
    ))

conn.commit()
print(f"Inserted {len(profiles)} device profiles successfully.")

cursor.close()
conn.close()
