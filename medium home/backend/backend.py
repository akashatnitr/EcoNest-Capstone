from flask import Flask, jsonify, request
import mysql.connector
from mysql.connector import pooling
import json
from datetime import datetime
from dotenv import load_dotenv
import os

app = Flask(__name__)

load_dotenv()

db_config = {
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'host': os.getenv('DB_HOST'),
    'database': os.getenv('DB_NAME')
}

def get_db_connection():
    """Get connection from pool"""
    try:
        connection = mysql.connector.connect(**db_config)
        if connection.is_connected():
            return connection
        else:
            print("Failed to get connection from pool")
            return None
    except mysql.connector.Error as e:
        print(f"Error getting connection: {e}")
        return None
    
# -----------------------------
# Room endpoints
# -----------------------------
@app.route("/rooms/add", methods=["POST"])
def add_room():
    data = request.get_json()
    
    if not data or "name" not in data:
        return jsonify({"error": "Missing 'name' in request body"}), 400

    name = data["name"]
    description = data.get("address", "")

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500
    
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "INSERT INTO rooms (name, description) VALUES (%s, %s)",
            (name, description)
        )
        conn.commit()
        room_id = cursor.lastrowid

        return jsonify({"room_id": room_id, "message": "Room created successfully"}), 201

    except Exception as e:
        conn.rollback()

        if "Duplicate entry" in str(e):
            cursor.execute("SELECT id FROM rooms WHERE name = %s", (name,))
            result = cursor.fetchone()
            return jsonify({
                "error": "Room already exists",
                "room_id": result["id"]
            }), 400

        return jsonify({"error": str(e)}), 500
    
    finally:
        cursor.close()
        conn.close()



@app.route("/rooms/get_id", methods=["POST"])
def get_room_id():
    data = request.get_json()
    
    if not data or "name" not in data:
        return jsonify({"error": "Missing 'name' in request body"}), 400

    room_name = data["name"]

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM rooms WHERE name = %s", (room_name,))
        result = cursor.fetchone()

        if result:
            return jsonify({"room_id": result["id"]}), 200
        else:
            return jsonify({"error": f"No room found with name '{room_name}'"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

@app.route("/rooms/list", methods=["GET"])
def list_rooms():
    """Get all rooms"""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, description FROM rooms ORDER BY name")
        rooms = cursor.fetchall()
        return jsonify({"rooms": rooms}), 200

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

    if not data:
        return jsonify({"error": "Data not received"}), 400
    
    # Validate required fields
    if "name" not in data:
        return jsonify({"error": "Missing 'name' in request body"}), 400
    if "ip_address" not in data:
        return jsonify({"error": "Missing 'ip_address' in request body"}), 400
    if "room_id" not in data:
        return jsonify({"error": "Missing 'room_id' in request body"}), 400
    if "device_type" not in data:
        return jsonify({"error": "Missing 'device_type' in request body"}), 400
    
    # Validate device_type (must match ENUM)
    valid_types = ['smart_plug', 'motion_sensor', 'sound_sensor', 'other']
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
        
        # Verify room_id exists
        cursor.execute("SELECT id FROM rooms WHERE id = %s", (data['room_id'],))
        if not cursor.fetchone():
            return jsonify({'error': f'Invalid room_id: {data["room_id"]}'}), 400
        
        # Insert device
        cursor.execute(
            """
            INSERT INTO devices (name, ip_address, room_id, device_type, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
            """,
            (
                data['name'],
                data['ip_address'],
                data['room_id'],
                data['device_type']
            )
        )
        conn.commit()
        device_id = cursor.lastrowid
        
        return jsonify({
            'device_id': device_id,
            'message': f'Device {data["name"]} added successfully'
        }), 201
        
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
        
    finally:
        cursor.close()
        conn.close()

@app.route("/devices/list/<int:room_id>", methods=["GET"])
def list_devices(room_id):
    """Get all devices for a room"""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, ip_address, device_type, is_active
            FROM devices 
            WHERE room_id = %s
            ORDER BY name
            """,
            (room_id,)
        )
        devices = cursor.fetchall()
        return jsonify({"devices": devices}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

@app.route("/devices/toggle", methods=["POST"])
def toggle_device():
    """Toggle a device on/off"""
    conn = get_db_connection()

    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    data = request.get_json()

    if not data or 'device_id' not in data:
        return jsonify({"error": "Missing 'device_id' in request body"}), 400
    
    device_id = data['device_id']

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT is_active 
            FROM Devices 
            WHERE id = %s;
            """,
            (device_id,)
        )
        result = cursor.fetchone()

        if not result or 'is_active' not in result:
            return jsonify({"error": f"No device found with device_id '{device_id}'"}), 404
        
        is_active = result['is_active']

        cursor.execute(
            """
            UPDATE Devices
            SET is_active = %s
            WHERE id = %s
            """,
            (not is_active, device_id)
        )
        conn.commit()

        return jsonify({
            'device_id': device_id,
            'message': f'Device is_active is now {not is_active}'
        }), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

@app.route("/devices/status", methods=["GET"])
def get_device_status():
    """Toggle a device on/off"""
    conn = get_db_connection()

    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    data = request.get_json()

    if not data or 'device_id' not in data:
        return jsonify({"error": "Missing 'device_id' in request body"}), 400
    
    device_id = data['device_id']

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT is_active 
            FROM Devices 
            WHERE id = %s;
            """,
            (device_id,)
        )
        result = cursor.fetchone()

        if not result or 'is_active' not in result:
            return jsonify({"error": f"No device found with device_id '{device_id}'"}), 404
        
        return jsonify({"device_id": device_id, "is_active": result['is_active']}), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

        
# -----------------------------
# Sensor readings
# -----------------------------
# Map device_type to required data keys

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

            # Lookup device to get room_id and device_type
            cursor.execute(
                "SELECT room_id, device_type, is_active FROM devices WHERE id = %s",
                (device_id,)
            )
            result = cursor.fetchone()
            
            if not result:
                errors.append(f"Reading {i} has invalid device_id {device_id}")
                continue
            
            if not result['is_active']:
                errors.append(f"Reading {i} device {device_id} is not active")
                continue
            
            device_type = result['device_type']
            room_id = result['room_id']
            # Validate data keys based on device_type

            # Insert into sensor_readings (timestamp auto-generated)
            cursor.execute(
                """
                INSERT INTO sensor_readings (device_id, room_id, data)
                VALUES (%s, %s, %s)
                """,
                (device_id, room_id, json.dumps(data_payload))
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