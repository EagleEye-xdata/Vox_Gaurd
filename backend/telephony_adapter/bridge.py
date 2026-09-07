import httpx
import logging
from .config import config

logger = logging.getLogger(__name__)

class IngestionBridge:
    def __init__(self):
        self.gateway_url = config.GATEWAY_URL
        self.client = httpx.Client(timeout=10.0)

    def start_session(self, filename: str, label: str) -> str:
        """Starts a session on the Go Gateway."""
        url = f"{self.gateway_url}/api/v1/stream/start"
        payload = {
            "filename": filename,
            "label": label,
            "simulate_detector_failure": False,
            "simulate_adversarial_input": False
        }
        
        response = self.client.post(url, json=payload)
        if response.status_code != 200:
            logger.error(f"Failed to start session: {response.text}")
            response.raise_for_status()
            
        data = response.json()
        return data.get("call_id", "")

    def stop_session(self, call_id: str):
        """Stops an active session."""
        if not call_id:
            return
            
        url = f"{self.gateway_url}/api/v1/stream/{call_id}/stop"
        response = self.client.post(url)
        if response.status_code != 200:
            logger.error(f"Failed to stop session: {response.text}")
