from flask import Flask, jsonify, request
import mysql.connector
from mysql.connector import pooling
import json
from datetime import datetime

app = Flask(__name__)

# Database configuration with SSL
db_config = {
    'host': "econest.cz00244kc0f6.us-east-2.rds.amazonaws.com",
    'user': "backend_user",
    'password': "datac0113ct0r",
    'database': "econest",
    'port': 3306,
    'ssl_disabled': False  # Enable SSL/TLS
}

# Connection pool for better performance
connection_pool = pooling.MySQLConnectionPool(
    pool_name="econest_pool",
    pool_size=5,  # Maintain 5 connections
    pool_reset_session=True,
    **db_config
)

def get_db_connection():
    """Get connection from pool"""
    try:
        connection = connection_pool.get_connection()
        if connection.is_connected():
            return connection
        else:
            print("Failed to get connection from pool")
            return None
    except mysql.connector.Error as e:
        print(f"Error getting connection: {e}")
        return None

# -----------------------------
# Home endpoints
# -----------------------------
@app.route("/homes/add", methods=["POST"])
def add_home():
    data = request.get_json()
    
    if not data or "name" not in data:
        return jsonify({"error": "Missing 'name' in request body"}), 400

    name = data["name"]
    address = data.get("address", "")

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        
        # Check if home already exists
        cursor.execute("SELECT id FROM homes WHERE name = %s", (name,))
        result = cursor.fetchone()
        if result:
            return jsonify({"error": "Home already exists", "home_id": result["id"]}), 400

        # Insert home
        cursor.execute(
            "INSERT INTO homes (name, address) VALUES (%s, %s)",
            (name, address)
        )
        conn.commit()
        home_id = cursor.lastrowid
        
        return jsonify({"home_id": home_id, "message": "Home created successfully"}), 201

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

@app.route("/homes/get_id", methods=["POST"])
def get_home_id():
    data = request.get_json()
    
    if not data or "name" not in data:
        return jsonify({"error": "Missing 'name' in request body"}), 400

    home_name = data["name"]

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM homes WHERE name = %s", (home_name,))
        result = cursor.fetchone()

        if result:
            return jsonify({"home_id": result["id"]}), 200
        else:
            return jsonify({"error": f"No home found with name '{home_name}'"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

@app.route("/homes/list", methods=["GET"])
def list_homes():
    """Get all homes"""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, address FROM homes ORDER BY name")
        homes = cursor.fetchall()
        return jsonify({"homes": homes}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

# -----------------------------
# Device endpoints
# -----------------------------
@app.route("/devices/add", methods=["POST"])
def add_device():
    data = request.get_json()
    
    # Validate required fields
    required_fields = ['device_name', 'ip_address', 'location', 'device_type', 'home_id']
    for f in required_fields:
        if f not in data:
            return jsonify({'error': f'Missing {f}'}), 400
    
    # Validate device_type (must match ENUM)
    valid_types = ['smart_plug', 'motion_sensor', 'sound_sensor']
    device_type = data['device_type']
    if device_type not in valid_types:
        return jsonify({
            'error': f'Invalid device_type. Must be one of: {", ".join(valid_types)}'
        }), 400

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        
        # Verify home_id exists
        cursor.execute("SELECT id FROM homes WHERE id = %s", (data['home_id'],))
        if not cursor.fetchone():
            return jsonify({'error': f'Invalid home_id: {data["home_id"]}'}), 400
        
        # Insert device with is_active defaulting to TRUE
        cursor.execute(
            """
            INSERT INTO devices (name, ip_address, location, device_type, home_id, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            """,
            (
                data['device_name'],
                data['ip_address'],
                data['location'],
                data['device_type'],
                data['home_id']
            )
        )
        conn.commit()
        device_id = cursor.lastrowid
        
        return jsonify({
            'device_id': device_id,
            'message': f'Device {data["device_name"]} added successfully'
        }), 201
        
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
        
    finally:
        cursor.close()
        conn.close()

@app.route("/devices/list/<int:home_id>", methods=["GET"])
def list_devices(home_id):
    """Get all devices for a home"""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, ip_address, location, device_type, is_active
            FROM devices 
            WHERE home_id = %s
            ORDER BY location, name
            """,
            (home_id,)
        )
        devices = cursor.fetchall()
        return jsonify({"devices": devices}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

@app.route("/devices/toggle/<int:device_id>", methods=["POST"])
def toggle_device(device_id):
    """Toggle device active status"""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        
        # Get current status
        cursor.execute("SELECT is_active FROM devices WHERE id = %s", (device_id,))
        result = cursor.fetchone()
        
        if not result:
            return jsonify({"error": f"Device {device_id} not found"}), 404
        
        # Toggle status
        new_status = not result['is_active']
        cursor.execute(
            "UPDATE devices SET is_active = %s WHERE id = %s",
            (new_status, device_id)
        )
        conn.commit()
        
        return jsonify({
            "device_id": device_id,
            "is_active": new_status,
            "message": f"Device {'activated' if new_status else 'deactivated'}"
        }), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

# -----------------------------
# Sensor readings
# -----------------------------
# Map device_type to required data keys
REQUIRED_KEYS = {
    "smart_plug": ["power", "voltage", "current"],
    "motion_sensor": ["motion"],
    "sound_sensor": ["sound_level"]
}

@app.route("/readings/add", methods=["POST"])
def add_readings():
    """Add sensor readings (batch or single)"""
    data = request.get_json()
    
    # Support both single reading and list of readings
    if isinstance(data, dict):
        readings = [data]
    elif isinstance(data, list):
        readings = data
    else:
        return jsonify({"error": "Request body must be a reading object or list of readings"}), 400

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    cursor = conn.cursor(dictionary=True)
    inserted_count = 0
    errors = []

    try:
        for i, r in enumerate(readings, start=1):
            device_id = r.get("device_id")
            data_payload = r.get("data")
            
            if not device_id or not data_payload:
                errors.append(f"Reading {i} missing device_id or data")
                continue

            # Lookup device to get home_id and device_type
            cursor.execute(
                "SELECT home_id, device_type, is_active FROM devices WHERE id = %s",
                (device_id,)
            )
            result = cursor.fetchone()
            
            if not result:
                errors.append(f"Reading {i} has invalid device_id {device_id}")
                continue
            
            if not result['is_active']:
                errors.append(f"Reading {i} device {device_id} is not active")
                continue
            
            home_id = result['home_id']
            device_type = result['device_type']

            # Validate data keys based on device_type
            required_keys = REQUIRED_KEYS.get(device_type, [])
            missing_keys = [k for k in required_keys if k not in data_payload]
            if missing_keys:
                errors.append(f"Reading {i} missing keys for {device_type}: {missing_keys}")
                continue

            # Insert into sensor_readings (timestamp auto-generated)
            cursor.execute(
                """
                INSERT INTO sensor_readings (device_id, home_id, data)
                VALUES (%s, %s, %s)
                """,
                (device_id, home_id, json.dumps(data_payload))
            )
            inserted_count += 1

        conn.commit()
        
        status_code = 201 if inserted_count > 0 else 400
        return jsonify({
            "message": f"Inserted {inserted_count} reading(s)",
            "total_submitted": len(readings),
            "errors": errors if errors else None
        }), status_code

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    app.run(debug=True)
