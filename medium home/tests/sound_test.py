import time
from datetime import datetime
import RPi.GPIO as GPIO

SOUND_PIN = 17          # GPIO17 (physical pin 11)
LOG_FILE = "sound_log.txt"

GPIO.setmode(GPIO.BCM)
GPIO.setup(SOUND_PIN, GPIO.IN)

print("Logging sound events... Press CTRL+C to stop.")

last_state = 0  # assume quiet at start

try:
    with open(LOG_FILE, "a") as f:
        while True:
            state = GPIO.input(SOUND_PIN)  # 0 = quiet, 1 = sound
            if state == 1 and last_state == 0:
                # Rising edge: quiet -> sound
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                line = f"{timestamp} - Sound detected\n"
                print(line, end="")
                f.write(line)
                f.flush()
            last_state = state
            time.sleep(0.01)  # 10ms loop; adjust if needed

except KeyboardInterrupt:
    print("\nStopping.")
finally:
    GPIO.cleanup()
