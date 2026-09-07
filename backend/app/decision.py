"""Decision Service for VoxGuard.

Implements the specification in docs/02-API_CONTRACTS.md §8 (DR-013):
- Maps SessionVerdict + policy pack + context into an actionable Decision.
- Emits structured required_actions, reason_codes, and non-accusatory human explanations (Invariant 12).
- Maintains a monotonic WAL sequence number and origin cryptographic signature.
- Supports supervisor overrides with mandatory role and reason audit logging.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import hmac
from .risk_scoring import POLICY

SIGNING_KEY = b"voxguard-demo-origin-signing-secret-key-2026"


def _sign_decision(payload: str) -> str:
    """Produces an HMAC-SHA256 origin signature for the decision record."""
    return "hmac-sha256:" + hmac.new(SIGNING_KEY, payload.encode("utf-8"), sha256).hexdigest()


class DecisionService:
    def __init__(self):
        self._wal_seq = 1000
        self._overrides: dict[str, dict] = {}

    def _next_wal_seq(self) -> int:
        self._wal_seq += 1
        return self._wal_seq

    def decide(self, session_verdict: dict, context: dict | None = None, policy: dict = POLICY) -> dict:
        """Computes automated decision and required actions for a session verdict."""
        session_id = session_verdict.get("session_id", "unknown-session")
        
        # Check if an active override exists for this session
        if session_id in self._overrides:
            override = self._overrides[session_id]
            return {
                "session_id": session_id,
                "decision": override["decision"],
                "required_actions": override["required_actions"],
                "reason_code": f"OVERRIDE_{override['reason_code']}",
                "explanation_for_human": f"Supervisor Override by {override['supervisor_id']} ({override['role']}): {override['reason']}",
                "reversible": True,
                "policy_version": policy["version"],
                "wal_seq": override["wal_seq"],
                "origin_signature": override["origin_signature"],
                "overridden": True,
                "override_details": override,
            }

        band = session_verdict.get("band", "UNKNOWN")
        score = session_verdict.get("session_score")
        context = context or {}
        txn_val = context.get("transaction_value", 0) or 0
        new_beneficiary = context.get("beneficiary_is_new", False)
        attestation = context.get("caller_attestation", "UNKNOWN")
        degraded = session_verdict.get("degraded", False)

        wal_seq = self._next_wal_seq()

        if band == "HIGH":
            if txn_val > 100_000 or new_beneficiary:
                decision = "BLOCK"
                reason_code = "SYNTHETIC_HIGH_VALUE_OR_NEW_BENEFICIARY_RISK"
                explanation = "Elevated synthetic speech probability on a high-value or new beneficiary request requires immediate transaction hold."
                actions = [
                    {"type": "hold_transaction", "channel": "core_banking", "auto_execute": True},
                    {"type": "callback_verification", "channel": "registered_phone", "mandatory": True},
                    {"type": "supervisor_review", "channel": "fraud_ops"}
                ]
            else:
                decision = "STEP_UP"
                reason_code = "SYNTHETIC_SPEECH_LIKELY_STEP_UP_REQUIRED"
                explanation = "Analysis indicates elevated synthetic speech markers. Multi-factor verification is required before continuing."
                actions = [
                    {"type": "mfa_push_or_otp", "channel": "registered_device", "mandatory": True},
                    {"type": "out_of_band_prompt", "channel": "banking_app"}
                ]
        elif band == "MEDIUM":
            if attestation != "VERIFIED" or new_beneficiary:
                decision = "STEP_UP"
                reason_code = "ELEVATED_RISK_UNVERIFIED_CALLER"
                explanation = "Moderate acoustic risk combined with unverified caller status requires step-up confirmation."
                actions = [
                    {"type": "otp_challenge", "channel": "registered_device", "mandatory": True}
                ]
            else:
                decision = "WARN"
                reason_code = "MODERATE_RISK_AGENT_CAUTION"
                explanation = "Moderate risk factors observed. Proceed with standard security verification protocol."
                actions = [
                    {"type": "agent_caution_banner", "channel": "operator_console"},
                    {"type": "security_question_step", "channel": "call_script"}
                ]
        elif band == "LOW":
            decision = "ALLOW"
            reason_code = "ROUTINE_LOW_RISK_ALLOW"
            explanation = "Acoustic and contextual signals meet low-risk threshold. Routine processing permitted."
            actions = []
        else:  # UNKNOWN or Degraded
            decision = policy.get("default_degraded_decision", "WARN")
            reason_code = "INSUFFICIENT_EVIDENCE_DEGRADED_DEFAULT" if degraded else "UNASSESSED_CALL_START"
            explanation = "Assessment incomplete or operating in degraded mode. Caution advised until sufficient voiced audio is assessed."
            actions = [{"type": "agent_advisory", "channel": "operator_console"}]

        signature = _sign_decision(f"{session_id}:{decision}:{band}:{wal_seq}:{reason_code}")

        return {
            "session_id": session_id,
            "decision": decision,
            "required_actions": actions,
            "reason_code": reason_code,
            "explanation_for_human": explanation,
            "reversible": True,
            "policy_version": policy["version"],
            "wal_seq": wal_seq,
            "origin_signature": signature,
            "overridden": False,
            "override_details": None,
        }

    def override(self, session_id: str, decision: str, reason: str, supervisor_id: str, role: str) -> dict:
        """Registers an audited supervisor override for a session decision."""
        valid_decisions = {"ALLOW", "WARN", "STEP_UP", "ESCALATE", "BLOCK"}
        if decision not in valid_decisions:
            raise ValueError(f"Invalid decision '{decision}'. Must be one of {valid_decisions}")
        if not reason or len(reason.strip()) < 5:
            raise ValueError("A documented justification reason (minimum 5 characters) is required for supervisor overrides.")
        if role not in {"SUPERVISOR", "FRAUD_ANALYST", "INCIDENT_COMMANDER", "ADMIN"}:
            raise PermissionError(f"Role '{role}' is not authorized to override automated decisions.")

        wal_seq = self._next_wal_seq()
        ts = datetime.now(timezone.utc).isoformat()
        actions = []
        if decision == "ALLOW":
            actions = [{"type": "override_release", "authorized_by": supervisor_id}]
        elif decision in {"STEP_UP", "ESCALATE"}:
            actions = [{"type": "manual_investigation_required", "assignee": supervisor_id}]
        elif decision == "BLOCK":
            actions = [{"type": "manual_fraud_freeze", "authorized_by": supervisor_id}]

        signature = _sign_decision(f"OVERRIDE:{session_id}:{decision}:{wal_seq}:{supervisor_id}")

        override_record = {
            "session_id": session_id,
            "decision": decision,
            "required_actions": actions,
            "reason_code": "MANUAL_SUPERVISOR_ACTION",
            "reason": reason,
            "supervisor_id": supervisor_id,
            "role": role,
            "timestamp": ts,
            "wal_seq": wal_seq,
            "origin_signature": signature,
        }
        self._overrides[session_id] = override_record
        return override_record

    def clear_override(self, session_id: str) -> bool:
        """Clears an active override if present."""
        if session_id in self._overrides:
            del self._overrides[session_id]
            return True
        return False


decision_service = DecisionService()
