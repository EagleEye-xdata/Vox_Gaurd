"""Alert Service and Resolution Subsystem for VoxGuard.

Implements the contract in docs/02-API_CONTRACTS.md §10 & §10.1 (DR-008, DR-019):
- Manages deduplicated, idempotency-keyed alerts (one per band escalation).
- Enforces Human-in-the-loop SLA tracking (Ack & Resolve deadlines).
- Supports resolution outcomes: CONFIRMED_FRAUD, FALSE_POSITIVE, INCONCLUSIVE.
- Integrates customer appeal filing to guarantee compliance and fairness.
"""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)


def calculate_sla_deadlines(band: str, created_at_dt: datetime) -> tuple[str, str]:
    """Computes SLA Ack & Resolve timestamps according to docs/02 §10.1."""
    if band == "HIGH":
        ack_dt = created_at_dt + timedelta(minutes=2)
        resolve_dt = created_at_dt + timedelta(minutes=30)
    elif band == "MEDIUM":
        ack_dt = created_at_dt + timedelta(minutes=15)
        resolve_dt = created_at_dt + timedelta(hours=4)
    else:
        ack_dt = created_at_dt + timedelta(hours=1)
        resolve_dt = created_at_dt + timedelta(hours=24)
    return ack_dt.isoformat(), resolve_dt.isoformat()


def make_alert(call_id: str, score: float, band: str, alert_id: str) -> dict:
    """Creates a new structured alert record with SLA tracking."""
    now_dt = datetime.now(timezone.utc)
    ack_sla, resolve_sla = calculate_sla_deadlines(band, now_dt)
    
    logger.info("Security Alert created for call %s: band=%s score=%.2f id=%s", call_id, band, score, alert_id)
    
    return {
        "id": alert_id,
        "call_id": call_id,
        "risk_score": score,
        "band": band,
        "status": "active",
        "created_at": now_dt.isoformat(),
        "updated_at": now_dt.isoformat(),
        "sla_ack_deadline": ack_sla,
        "sla_resolve_deadline": resolve_sla,
        "assigned_to": None,
        "resolution": None,
        "appeals": [],
        "message": "Additional verification required. Voice synthesis and contextual anomalies detected.",
        "recommendations": [
            "Out-of-band callback using verified profile number",
            "Require in-app biometric / hardware token MFA",
            "Hold high-value transactions pending supervisor sign-off"
        ],
        "auto_block": band == "HIGH",
        "notification_channel": "DashboardChannel (with Webhook retry lane)"
    }


def assign_alert(alert: dict, assignee_id: str) -> dict:
    """Assigns an alert to an investigator, moving it to 'assigned' status."""
    alert["assigned_to"] = assignee_id
    if alert["status"] == "active":
        alert["status"] = "assigned"
    alert["updated_at"] = datetime.now(timezone.utc).isoformat()
    return alert


def resolve_alert(alert: dict, outcome: str, notes: str, resolver_id: str) -> dict:
    """Resolves an alert with a mandatory closed-enum outcome for feedback loop training."""
    valid_outcomes = {"CONFIRMED_FRAUD", "FALSE_POSITIVE", "INCONCLUSIVE"}
    if outcome not in valid_outcomes:
        raise ValueError(f"Invalid resolution outcome '{outcome}'. Must be one of {valid_outcomes}")
    if not notes or len(notes.strip()) < 3:
        raise ValueError("Investigation notes are mandatory when resolving a security alert.")

    alert["status"] = "resolved"
    alert["updated_at"] = datetime.now(timezone.utc).isoformat()
    alert["resolution"] = {
        "outcome": outcome,
        "notes": notes,
        "resolver_id": resolver_id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    return alert


def lodge_appeal(alert: dict, reason: str, appellant_type: str = "CUSTOMER", contact_info: str | None = None) -> dict:
    """Lodges an appeal ticket against an alert or stepped-up decision per docs/09 §5."""
    if not reason or len(reason.strip()) < 5:
        raise ValueError("A detailed explanation is required to lodge an appeal.")
    
    appeal_ticket = {
        "appeal_id": f"app-{uuid4().hex[:8]}",
        "alert_id": alert["id"],
        "call_id": alert["call_id"],
        "appellant_type": appellant_type,
        "reason": reason,
        "contact_info": contact_info or "Not provided",
        "status": "PENDING_REVIEW",
        "sla_resolution_deadline": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        "filed_at": datetime.now(timezone.utc).isoformat(),
    }
    alert["appeals"].append(appeal_ticket)
    alert["updated_at"] = datetime.now(timezone.utc).isoformat()
    return appeal_ticket
