# UX Specification (NEW in v2)

> Fixes DR-034, DR-035. v1 contained one bullet — "basic dashboard" — for a product whose appeal
> path is a *compliance control* and whose primary artefact is a real-time accusation-adjacent
> judgement about a person. This is not decoration; it is where the system's ethics become visible.

## 1. Who this is for

| User | Context | Needs in one line |
|---|---|---|
| **Call-handling agent** (bank, contact centre) | Mid-call, on the phone, one glance available | "Do I need to do something differently in the next ten seconds?" |
| **Fraud analyst** | Triaging a queue, several minutes per case | "Is this real, and can I justify my decision to someone else?" |
| **Tenant admin** | Occasional, configuration | "What will happen if I move this threshold?" |
| **Auditor / DPO** | Rare, adversarial | "Show me exactly what the system knew and when." |
| **The person being scored** | Received a step-up challenge; possibly annoyed, possibly innocent | "Why, and how do I get past this without being treated as a suspect?" |

The last row is the one that gets skipped and the one that carries legal risk.

## 2. Design direction

Grounded in the subject: this is **live instrumentation for a high-stakes judgement**, closer to a
clinical monitor or an audio console than to a SaaS analytics dashboard. It should feel calm,
legible at a glance, and quietly serious. It should not feel like a threat display — an interface
that looks alarming makes agents over-trust it.

**Palette** — a low-luminance neutral base so that colour, when it appears, means something.

```
--surface-0    #12161C   base
--surface-1    #1A2029   panels
--surface-2    #232B36   raised
--ink-hi       #E8EDF4   primary text
--ink-lo       #97A3B4   secondary text
--band-low     #4FA88B   (green-teal, desaturated)
--band-medium  #C99A3C   (amber, desaturated)
--band-high    #C4564B   (clay red — muted, not alarm red)
--band-unknown #6B7686   (grey — deliberately inert)
--evidence     #7FA6D9   (used only for explainability bars)
```

Band colours are **desaturated on purpose**. A saturated red on a screen an agent stares at all
day stops meaning anything within a week.

**Type** — one family, two roles. A humanist sans for the interface (IBM Plex Sans or Inter) and
its tabular-figure variant for all numerics, so scores do not jitter as they update every second.
Score readouts use tabular figures without exception; a shifting digit width in a live-updating
number reads as instability.

**Motion** — one orchestrated moment only: the band-change transition. Everything else is
instantaneous. A per-second score readout that animates is unreadable. Respect
`prefers-reduced-motion` by replacing the transition with a static state change.

## 3. The band vocabulary — never colour alone

Every band is encoded **three** ways: colour, a distinct shape/glyph, and a text label. Colour
alone fails for ~8% of male users and fails entirely in a printed audit export.

```
LOW      ●  Low          UNKNOWN  ◌  Not assessed
MEDIUM   ◆  Elevated     HIGH     ▲  High
```

Note the label wording. `UNKNOWN` renders as **"Not assessed"**, never "Safe", never "Clear",
never an empty state that reads as fine. This is the UI half of the architectural rule that
absence of evidence is not evidence of safety. *(DR-002)*

## 4. Screens

### 4.1 Live monitor (agent view) — the ten-second screen
A single strip, not a dashboard. Designed to be understood peripherally.

```
┌────────────────────────────────────────────────────────────────┐
│  ◆ Elevated · 58        Verify identity before proceeding      │
│  ▁▂▃▅▆▅▄▃▄▆▇█▇▆  ← last 60s                                    │
│  Synthetic speech likely (0.74) · Caller ID unverified          │
│  [ Send OTP ]  [ Call back on file number ]  [ Mark as fine ]   │
└────────────────────────────────────────────────────────────────┘
```

Rules:
- **One recommended action, always.** A band with no action is noise. HIGH without a next step
  trains agents to ignore it.
- The sparkline is the most valuable element: it shows *when* things changed, which is what an
  agent needs to correlate with what the caller just said.
- **"Mark as fine" is always present**, one click, no dialog. Agent dissent is your highest-quality
  false-positive signal and you only get it if it is frictionless.
- No raw model internals here. Agents are not model evaluators.

### 4.2 Alert triage queue (analyst view)
Columns: band · score · trend arrow · what triggered it (one phrase) · elapsed vs SLA · assignee.

Sort by **SLA burn**, not by score. The 30-minute-old MEDIUM matters more than the fresh HIGH
someone is already on. Filters: band, degraded-only, floor-triggered-only, appealed-only.

Empty state: *"No open alerts. Last 24h: 41 reviewed, 6 confirmed."* — an empty screen should
report, not just say "nothing here."

### 4.3 Case detail — the explainability screen
This is the screen that decides whether the product is defensible.

```
Session 8f21…  ▲ High · 84 (peak)                    [Confirm fraud] [Not fraud] [Inconclusive]
─────────────────────────────────────────────────────────────────────────────────────────
Timeline   ├──── Low ────┼── Elevated ──┼──────── High ────────┤   ▶ play band changes
           0:00        0:41           0:58                   2:14

Why this score
  Synthetic speech       0.74  ████████████░░░░░░░  33.3 pts
  Speaker mismatch       0.61  ██████████░░░░░░░░░  18.3 pts
  Context risk           0.72  ████████████░░░░░░░  18.0 pts
                                                    ─────────
                                                    69.6 pts
  Floor applied: adversarial input detected → raised to 84 ⓘ

What was unavailable
  — Transaction context: service timeout at 0:52 (17 windows affected)

Versions   detector d-2.1.0 · calibrator c-1.0.2 · policy banking@1.4.0 · code 9f3a1c
```

Requirements:
- Factor bars are the API's `contributing_factors`, rendered directly. If they do not sum to the
  base score the UI shows an error — this is the visible half of test T-2.6.
- **Applied floors are shown separately from evidence.** A reviewer must be able to tell "the model
  found this" from "a safety rule raised this." v1 could not distinguish them at all.
- Degraded windows are shown as a gap in the timeline, not smoothed over.
- Versions are always visible. An analyst who cannot name the policy version cannot defend the
  decision six months later.
- **No audio playback by default.** Raw audio does not persist (`06 §2`), so the UI must not imply
  it does. If a tenant opts into recording, playback is a separate, permission-gated, access-logged
  feature with its own visible indicator.

### 4.4 Enrolment & consent (subject-facing)
- Consent text in plain language, above the fold, before the record button — not behind a link.
- Three facts before any recording: what is stored (a mathematical representation, not a recording),
  how long, and how to delete it.
- The delete control lives in the same place as the enrol control, permanently. Consent you cannot
  find how to withdraw is not consent.
- Progress feedback during capture, and a re-record option. A stressed enrolment produces a poor
  reference and a lifetime of false rejections.

### 4.5 Policy configuration (admin)
The dangerous screen. Changing a weight silently changes how thousands of people are treated.

- **Simulation before save**: "Applied to the last 30 days, this change would move 214 sessions
  from Elevated to High and 63 from High to Elevated." Never let a threshold be saved blind.
- Diff view on save; reason field required; change is audit-logged with actor.
- Weight sliders are constrained to sum-normalisable values and show the resulting effective
  weights after renormalisation, so admins see what actually happens when a signal is inactive.

### 4.6 Audit explorer (auditor)
Chain verification with a visible result: `✓ 1,204,881 records verified · chain intact · last
checkpoint 04:00 UTC`. A broken chain shows the exact `seq` where verification failed. Read-only,
enforced by role, with export that includes the version fields.

## 5. The appeal / step-up flow — a compliance control *(DR-035)*

**Copy rules, non-negotiable:**

| Never write | Write instead |
|---|---|
| "Fraud detected" | "We need to verify it's you" |
| "Your voice appears fake" | "We couldn't complete voice verification" |
| "Suspicious caller" | "Additional verification required" |
| "You have been blocked" | "We couldn't approve this by phone. Here's how to complete it." |

The system produces a probability. The interface must not report it as a finding about a person's
character. This is both an ethical line and the practical difference between an awkward call and a
defamation exposure.

**Flow requirements:**
- Step-up must be completable in under 60 seconds on the channel the person is already using.
- At least one alternative route for people who cannot complete the primary challenge — no
  smartphone, no OTP, a disability affecting speech. A verification system with a single modality
  excludes people, and excluded people are disproportionately the ones already underserved.
- Named human contact and an SLA (1 business hour ack, 24 h resolve) surfaced *in the flow*, not
  buried in a help centre.
- An appeal decision states what changed, in plain language.

## 6. States — specify all five for every component

Loading · empty · **degraded** · error · populated. The degraded state is the one teams skip and
the one this product needs most.

Degraded rendering, everywhere: a persistent inline strip reading
*"Limited assessment — transaction context unavailable since 0:52"*. Never a toast (it disappears),
never a silent omission. If the score is partial, every surface showing that score says so.

## 7. Real-time behaviour

- Verdict updates arrive ~1/second. **Do not re-render the whole panel.** Update the numeric and
  the sparkline only.
- Band changes announce via `aria-live="assertive"`; score ticks must be `aria-live="off"` or a
  screen-reader user hears a number read aloud every second, which makes the product unusable.
- Debounce visual band changes to match the hysteresis in `04-SESSION_SCORING_SPEC.md` §4. The UI
  must never flicker between bands — if it does, the hysteresis is not being applied and that is a
  backend bug surfacing visually.
- Connection loss shows *"Live assessment paused — reconnecting"*, never a frozen last-good score
  presented as current. A stale score displayed as live is the worst failure this UI can have.

## 8. Accessibility floor (WCAG 2.2 AA)

Contrast ≥ 4.5:1 for text, ≥ 3:1 for the band glyphs · full keyboard operation of the triage queue
including resolve actions · visible focus rings · no colour-only encoding (§3) ·
`prefers-reduced-motion` honoured · target size ≥ 24 px · live regions used correctly per §7 ·
screen-reader labels that state band *and* score ("High risk, 84 out of 100").

## 9. Phase 0 build order

Build in this sequence; each step is demonstrable on its own.
1. Live monitor strip (§4.1) — the demo centrepiece.
2. Case detail with factor bars (§4.3) — what turns the demo from a toy into a system.
3. Triage queue (§4.2) — minimal, no assignment.
4. Degraded state (§6) — the thing you show by killing a service on stage.

Enrolment, policy config, and audit explorer are Phase 1. Do not build six screens in five days.
