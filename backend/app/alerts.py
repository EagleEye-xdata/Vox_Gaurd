from datetime import datetime, timezone
from uuid import uuid4
import logging

def make_alert(call_id, score):
    logging.getLogger(__name__).info("Demo only: would send SMS via Twilio for call %s", call_id)
    return {"id": str(uuid4()), "call_id": call_id, "risk_score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(), "status": "active",
            "message": "Suspicious voice pattern detected. Verify with a callback or MFA before proceeding.",
            "recommendations": ["Callback using a trusted number", "Complete MFA", "Escalate to supervisor"],
            "auto_block": False, "notification": "Demo only: would send SMS via Twilio"}
