from pydantic import BaseModel, Field, ConfigDict, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Context(StrictModel):
    caller_attestation: str = Field(default="UNKNOWN", pattern="^(VERIFIED|KNOWN_UNVERIFIED|UNKNOWN)$")
    attestation_source: str = Field(default="NONE", pattern="^(STIR_SHAKEN_A|AUTHENTICATED_APP_SESSION|CALLER_ID_ONLY|NONE)$")
    transaction_value: float | None = Field(default=None, ge=0, le=1e12)
    transaction_currency: str = Field(default="INR", pattern="^[A-Z]{3}$")
    transaction_type: str = Field(default="wire_transfer", pattern="^(wire_transfer|card_payment|cash_withdrawal|account_change|none)$")
    beneficiary_is_new: bool = False
    request_urgency: str = Field(default="normal", pattern="^(low|normal|high)$")
    urgency_source: str = Field(default="POLICY_DERIVED", pattern="^(AGENT_ASSERTED|POLICY_DERIVED|CALLER_CLAIMED)$")
    confirmed_fraud_flags_90d: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_provenance(self):
        trusted = {"STIR_SHAKEN_A", "AUTHENTICATED_APP_SESSION"}
        if self.caller_attestation == "VERIFIED" and self.attestation_source not in trusted:
            raise ValueError("VERIFIED attestation requires an independently trusted source")
        if self.attestation_source == "CALLER_ID_ONLY" and self.caller_attestation == "VERIFIED":
            raise ValueError("Caller ID alone cannot produce VERIFIED attestation")
        return self

class Start(StrictModel):
    filename: str = Field(min_length=1, max_length=200)
    label: str = Field(default="Simulated call", min_length=1, max_length=80)
    context: Context = Field(default_factory=Context)
    interval: float = Field(default=1, ge=0.05, le=4)
    simulate_detector_failure: bool = False

class AudioChunk(StrictModel):
    samples: list[float] = Field(min_length=4000, max_length=64000)
    sample_rate: int = Field(default=16000, ge=16000, le=16000)

class Scores(StrictModel):
    spectral_score: float = Field(ge=0, le=1)
    prosody_score: float = Field(ge=0, le=1)
    speaker_match_score: float | None = Field(default=None, ge=0, le=1)
    context: Context = Field(default_factory=Context)

class AlertRequest(StrictModel):
    call_id: str

class LedgerEvent(StrictModel):
    call_id: str = Field(max_length=80)
    event_type: str = Field(pattern="^(observation|alert|escalation|call_completed)$")
    risk_score: float = Field(ge=0, le=100)
    band: str = Field(default="UNKNOWN", pattern="^(LOW|MEDIUM|HIGH|UNKNOWN)$")
    policy_version: str = Field(default="banking-demo@2.0.0", max_length=80)
    model_versions: dict[str, str] = Field(min_length=1)
