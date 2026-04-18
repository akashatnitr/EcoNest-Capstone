import sounddevice as sd
import numpy as np
import requests
import time
from collections import deque

API_URL = "http://127.0.0.1:5000/readings/add"
DEVICE_ID = 6

SAMPLE_RATE = 44100
DURATION = 0.5
SLEEP_SECONDS = 2

# Adaptive threshold settings
BASELINE_WINDOW = 15      # number of recent readings to average
SPIKE_MARGIN_DB = 10.0    # how much louder than baseline counts as a spike
MIN_SPIKE_LEVEL = -30.0   # optional floor so tiny changes in silence don't trigger

recent_levels = deque(maxlen=BASELINE_WINDOW)


def get_sound_level():
    recording = sd.rec(
        int(DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32"
    )
    sd.wait()

    rms = np.sqrt(np.mean(recording**2))

    if rms <= 0:
        db = -100.0
    else:
        db = 20 * np.log10(rms + 1e-9)

    return round(float(db), 2)


while True:
    try:
        sound_level = get_sound_level()

        if len(recent_levels) == 0:
            baseline = sound_level
        else:
            baseline = sum(recent_levels) / len(recent_levels)

        spike = (
            sound_level >= MIN_SPIKE_LEVEL and
            sound_level >= baseline + SPIKE_MARGIN_DB
        )

        payload = {
            "device_id": DEVICE_ID,
            "data": {
                "sound_level": sound_level,
                "spike": spike,
                "baseline": round(baseline, 2)
            }
        }

        r = requests.post(API_URL, json=payload, timeout=5)
        print(
            f"sound={sound_level} dB | baseline={baseline:.2f} dB | "
            f"spike={spike} | status={r.status_code}"
        )

        recent_levels.append(sound_level)

    except Exception as e:
        print("Error:", e)

    time.sleep(SLEEP_SECONDS)
