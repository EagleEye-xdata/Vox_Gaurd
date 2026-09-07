"""Put the backend directory on sys.path so the suite runs from the repo root or from backend/."""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
