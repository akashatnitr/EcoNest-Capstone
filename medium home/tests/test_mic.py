import sounddevice as sd
import numpy as np

duration = 2
fs = 44100

print("Recording...")
audio = sd.rec(int(duration * fs), samplerate=fs, channels=1)
sd.wait()

volume = np.sqrt(np.mean(audio**2))
print("Volume:", volume)
