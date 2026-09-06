from dataclasses import dataclass, field

def fuse(spectral, prosody, speaker=None, context=None):
    # Speaker score means match confidence; invert only when enrolled.
    acoustic = 0.55*spectral + 0.45*prosody if speaker is None else 0.45*spectral + 0.35*prosody + 0.2*(1-speaker)
    c = context or {}
    adjustment = (8 if c.get("known_number") is False else 0) + (8 if c.get("transaction_size", 0)>=100000 else 0)
    hour = c.get("hour")
    adjustment += 4 if hour is not None and (hour < 6 or hour >= 22) else 0
    return round(min(100, max(0, acoustic*100+adjustment)), 2)

@dataclass
class RollingRisk:
    value: float | None = None
    history: list = field(default_factory=list)
    streak: int = 0

    def update(self, score):
        self.value = score if self.value is None else 0.3*score + 0.7*self.value
        self.value = round(self.value, 2)
        self.history.append(self.value)
        self.streak = self.streak+1 if self.value >= 65 else 0
        return {"risk_score": self.value, "authenticity_score": round(100-self.value, 2),
                "history": self.history.copy(), "sustained_high_risk": self.streak >= 2 and len(self.history)>=3}
