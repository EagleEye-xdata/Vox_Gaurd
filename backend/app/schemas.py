from pydantic import BaseModel, Field, ConfigDict

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Context(StrictModel):
    transaction_size: float = Field(default=0, ge=0, le=1e12)
    known_number: bool = True
    hour: int | None = Field(default=None, ge=0, le=23)

class Start(StrictModel):
    filename: str = Field(min_length=1, max_length=200)
    label: str = Field(default="Simulated call", min_length=1, max_length=80)
    context: Context = Field(default_factory=Context)
    interval: float = Field(default=3, ge=0.1, le=4)

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
