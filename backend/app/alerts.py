from datetime import datetime, timezone
import logging

def make_alert(call_id, score, band, alert_id):
    logging.getLogger(__name__).info("Demo only: would send SMS via Twilio for call %s", call_id)
    return {"id": alert_id, "call_id": call_id, "risk_score": score, "band": band,
            "timestamp": datetime.now(timezone.utc).isoformat(), "status": "active",
            "message": "Additional verification required. Verify with a callback or MFA before proceeding.",
            "recommendations": ["Callback using a trusted number", "Complete MFA", "Escalate to supervisor"],
            "auto_block": False, "notification": "Demo only: would send SMS via Twilio"}
