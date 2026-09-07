# Cost Model & Unit Economics (NEW in v2)

> Fixes DR-038, DR-046. v1 contained no cost analysis at all — for a product whose core operation is GPU
> inference on every second of every call. This is the question that decides whether the system is
> a product or a science project, and it is the one a technical judge or investor asks first.

**All figures are illustrative order-of-magnitude at 2026 cloud pricing. Substitute your own
measured throughput before quoting any of them.** The method matters more than the numbers.

## 1. The cost driver

Cost scales with **voiced seconds**, not with calls. This is the single most important modelling
insight and the reason `04-SESSION_SCORING_SPEC.md` counts voiced audio only.

```
windows_per_call = voiced_seconds / hop_seconds          # hop = 1.0 s
inference_units  = windows_per_call × models_per_window  # detector + verifier = 2
```

A 6-minute call with 55% speech activity = ~198 voiced seconds = ~198 windows = ~396 inferences.
A naive design that scored every wall-clock second would run 720. A design that scored every 20 ms
frame would run 36,000 — and this is the mistake an unspecified implementation makes by default.

## 2. Worked GPU cost

Assumptions (replace with measurements):

| Parameter | Value |
|---|---|
| Detector: ~95M-param speech encoder + head, ONNX, fp16 | 18 ms/window on a mid-tier inference GPU |
| Verifier embedding | 9 ms/window |
| Effective batch utilisation at steady load | 60% |
| GPU cost | $0.75/hour |
| Effective GPU-seconds per window | (0.018 + 0.009) / 0.60 = 0.045 s |

```
Cost per voiced second  = 0.045 GPU-s × ($0.75 / 3600)  = $0.0000094
Cost per voiced minute  ≈ $0.00056
Cost per 6-min call (55% speech) ≈ $0.0019   ≈ ₹0.16
```

| Scale (calls/day, 6 min avg) | Voiced-min/day | GPU cost/day | GPU cost/month |
|---|---|---|---|
| 1,000 (pilot) | 3,300 | $1.85 | $56 |
| 100,000 (mid-size bank) | 330,000 | $185 | $5,550 |
| 5,000,000 (carrier) | 16.5M | $9,240 | $277,000 |

## 3. Where the cost actually goes at scale

GPU is rarely the largest line beyond pilot scale. Full picture:

| Line | Share at 100k calls/day | Note |
|---|---|---|
| GPU inference | ~35% | Falls with batching and quantisation |
| CPU: preprocessing, VAD, **diarisation** | ~25% | Diarisation is often the sleeper cost — it is per-second and CPU-bound |
| Network egress / media transport | ~15% | Carrier-scale audio ingress is a real line item |
| Postgres + WAL + audit storage | ~10% | Grows monotonically; 5–7 year retention compounds |
| Observability (traces at 1/s per session) | ~10% | **Sample traces.** Full tracing at per-second verdict cadence costs more than the inference |
| Kafka/async lane | ~5% | Cheap once audio is off it |

Two traps worth naming, because both have killed similar systems:
- **Tracing cost exceeding inference cost** *(DR-046)*. One trace per verdict at 1 Hz per session is an
  enormous volume. Head-sample at 1–5%, tail-sample all degraded and all HIGH sessions.
- **Audit storage compounding.** 5-year retention at carrier volume is billions of rows. Partition
  by month, and plan cold storage from the start rather than discovering it in year two.

## 4. Break-even sketch

At a plausible ₹0.50–₹2.00 per protected call in banking, gross margin at 100k calls/day is
comfortable; the binding constraint is not COGS but **false-positive cost**. Each false HIGH
consumes analyst time and customer goodwill:

```
FP cost/day = calls/day × FPR × (analyst_minutes × loaded_rate + churn_risk_cost)
```

At 100k calls/day and a 2% false-HIGH rate, that is 2,000 alerts/day. At 4 analyst-minutes each,
that is **133 analyst-hours per day** — roughly 17 full-time analysts, dwarfing the $185/day of
GPU. **A one-point reduction in false-positive rate is worth more than any inference
optimisation.** This is the number to put on the slide.

It is also why `03-RISK_SCORING_SPEC.md`'s hysteresis, calibration, and floor design are economic
decisions, not just engineering ones.

## 5. Cost-based denial of service

An authenticated tenant (or a compromised credential) can force unbounded inference spend. Controls:
- Per-tenant inference budget with a hard cap and a soft-cap alert.
- Admission control: shed at the ingestion boundary, never queue unboundedly into the GPU.
- Bill on voiced seconds so the pricing model and the cost model share a denominator — otherwise
  an attacker can make you unprofitable without exceeding any quota you measure.

Cross-reference: `07-SECURITY_ARCHITECTURE.md` §7.

## 6. Optimisation ladder (cheapest wins first)

1. **Score voiced audio only** — already in the design; roughly a 45% saving over wall-clock.
2. **Adaptive hop**: widen the hop to 2 s while the session is stably LOW, tighten to 1 s once
   anything is elevated. Typically 40–50% fewer inferences with negligible detection loss, because
   most calls are unremarkable most of the time.
3. **INT8 quantisation** of the detector: ~2× throughput; re-validate FAR/FRR *and* re-fit the
   calibrator afterwards — quantisation shifts calibration.
4. **Batching** across sessions: raises utilisation from ~60% toward ~85%; costs a few ms of
   latency, which the 235 ms budget headroom absorbs.
5. **Cascade**: a very cheap first-stage screen on every window, escalating to the full model only
   on ambiguity. Biggest win, most complexity, most risk of a bypass — do this last and red-team it,
   because the cheap stage becomes the attack surface.

## 7. Hackathon cost reality

Your actual cost is a laptop or one Colab/free-tier GPU, and it is effectively zero. Do not pretend
otherwise. What earns credit is showing you **know** the shape of the curve:

> "At 100k calls/day, GPU inference costs roughly $185/day. Our real cost driver is false
> positives — at 2% we would need 17 analysts. That is why we spent our effort on calibration and
> hysteresis rather than on model size."

That answer demonstrates systems thinking. A fabricated cost table does not.
