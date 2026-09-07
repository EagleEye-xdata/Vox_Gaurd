import os
import threading
import soundfile as sf
import numpy as np
import time

FIFO_PATH = "test_fifo.wav"

def writer():
    # Write a valid WAV header and some silence
    data = np.zeros((16000 * 5,), dtype=np.float32)
    with sf.SoundFile(FIFO_PATH, mode='w', samplerate=16000, channels=1, format='WAV', subtype='FLOAT') as f:
        f.write(data)

def reader():
    time.sleep(0.1)
    with sf.SoundFile(FIFO_PATH) as f:
        try:
            print(f"Frames: {len(f)}")
            f.seek(0)
            print("Seek succeeded")
        except Exception as e:
            print(f"Error: {e}")

if not os.path.exists(FIFO_PATH):
    os.mkfifo(FIFO_PATH)

t1 = threading.Thread(target=writer)
t2 = threading.Thread(target=reader)
t1.start()
t2.start()
t1.join()
t2.join()
os.remove(FIFO_PATH)
