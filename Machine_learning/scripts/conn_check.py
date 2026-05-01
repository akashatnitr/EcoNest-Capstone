import mysql.connector
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

try:
    conn = mysql.connector.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", "econest"),
        database=os.environ.get("DB_NAME", "econest"),
        port=int(os.environ.get("DB_PORT", 3306)),
        ssl_disabled=False
    )

    if conn.is_connected():
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        print("Connection successful!")
        print("Tables found:")
        for table in tables:
            print(f"  - {table[0]}")
        cursor.close()
        conn.close()

except mysql.connector.Error as e:
    print(f"Connection failed: {e}")