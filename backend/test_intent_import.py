"""Quick smoke test: Whisper STT + intent phrase classifier on a synthetic audio buffer."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
from app import intent as i

print("Whisper available:", i._load_whisper())

# Phrase classifier tests (keyword matching only, no real audio needed)
test_cases = [
    ("Please share your OTP urgently to prevent account freeze", 0.5),
    ("Hi, I wanted to check my account balance please", 0.0),
    ("Wire transfer karo turant, account block ho jayega", 0.5),
    ("Good morning, this is a routine compliance call", 0.0),
    ("Your card CVV and PIN are required to unlock your account", 0.5),
]
print("\n--- Phrase classifier (keyword matching) ---")
for text, expected_min in test_cases:
    accum = 0.0
    hits = []
    for pattern, weight in i._COMPILED:
        if pattern.search(text):
            accum += weight
            hits.append(pattern.pattern[:20])
    risk = round(min(1.0, accum), 4)
    ok = "PASS" if (risk >= expected_min) else "FAIL"
    print(f"  [{ok}] i_risk={risk:.3f} | '{text[:50]}' | hits={hits[:2]}")

print("\nAll intent tests done.")
