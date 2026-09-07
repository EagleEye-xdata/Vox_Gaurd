import os

class Config:
    PBX_HOST = os.environ.get("PBX_HOST", "0.0.0.0")
    PBX_PORT = int(os.environ.get("PBX_PORT", 8080))
    GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
    AUDIO_DIR = os.environ.get("AUDIO_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "demo_audio"))

config = Config()
