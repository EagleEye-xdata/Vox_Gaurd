# Consent Log

> **This file is the authority for invariant 14** (`CLAUDE.md`): no real person's voice may be
> recorded, enrolled, synthesised, or cloned unless there is a signed entry below.
> `06-DATA_PRIVACY_COMPLIANCE.md` §0 is the governing policy.
>
> This log is **test-enforced**. `backend/tests/` fails if `demo_audio/` contains a non-fixture
> `.wav` that has no corresponding entry in §3. A promise in a document is not a control; a
> failing test is.

---

## 1. Current status

**No human voice recordings are in use.** As of 2026-09-07 the project uses only:

| Source | Nature | Ethics status |
|---|---|---|
| `fixture-steady.wav`, `fixture-variable.wav`, `fixture-silence.wav` | Deterministic DSP sine/harmonic signals from `backend/generate_fixtures.py` | Not speech. No person involved. |
| `tts-*.wav` | Windows `System.Speech` synthetic scenario audio | Machine voice. No person cloned. |
| Public corpora (ASVspoof 2019 LA, In-The-Wild, MLAAD, IndicVoices, Common Voice) | Licensed research/open corpora | Consent handled by the corpus publisher under its licence. See `docs/HANDOFF.md` §7. |
| ElevenLabs scripted-attacker audio (`backend/app/elevenlabs_agent.py`, added 2026-09-08) | Hosted TTS of a **fictional** scam caller ("Rahul", a bank officer who does not exist), spoken by a stock or prompt-designed voice | No person cloned. **Enforced in code**, not by policy: `_assert_stock_voice` reads the voice's category from ElevenLabs and refuses `cloned` and `professional`; a voice whose category cannot be fetched is also refused, because unverified is not permitted. Refusals fall back to the DSP signal *and are reported*, never hidden. Nothing is written to `demo_audio/` — the audio exists only in memory. |

---

## 2. Rules — read before recording or synthesising anything

1. **Written, informed, revocable consent** is required from every person whose voice is recorded,
   enrolled, or used as a cloning reference. Verbal agreement is not sufficient.
2. **Never clone a judge, a professor, a celebrity, a public figure, or a bank executive.** It is
   the obvious demo idea and it is the one that gets a project disqualified.
3. Voice-cloning models in this project (**IndicF5**, **Indic Parler-TTS**) may only be pointed at
   speakers from an open corpus whose licence permits it, or at a person with a signed entry in §3.
4. Revocation is honoured within **24 hours**: the recording, any derived embedding, and any
   generated synthetic audio derived from that voice are hard-deleted.
5. Raw recordings are **never committed to git**. `.gitignore` excludes `demo_audio/*.wav`.
   A committed training clip in a public repo is a data breach.
6. A person's consent to *enrolment* is not consent to *cloning*. Record them separately.
7. **Hosted TTS is bound by rules 1–3 exactly as local models are.** Setting `ELEVENLABS_VOICE_ID`
   to a voice cloned from a real person is a rule-2 violation whether or not the clone was made
   here, and the code refuses it. Do not add an override; if you find yourself wanting one, the
   demo needs a different voice, not a weaker gate.

---

## 3. Signed consent entries

> Add one row per person. Do not add a voice file to `demo_audio/` before its row exists here.
> `Consent form` should reference a signed document held outside the repo (drive link, file
> reference, or physical form ID) — **never paste personal contact details into this file.**

| ID | Role | Date signed | Scope granted | Consent form ref | Revoked |
|---|---|---|---|---|---|
| _(none)_ | — | — | — | — | — |

**Scope vocabulary:** `RECORD` (genuine speech capture) · `ENROL` (speaker-verification reference
embedding) · `CLONE` (used as a TTS/voice-conversion reference) · `DEMO` (may appear in a public
demonstration).

---

## 4. Template — copy this when a form is signed

```
| P-01 | Team member | 2026-09-08 | RECORD, ENROL | consent-forms/P-01.pdf | no |
```

Then add the corresponding `.wav` files to `demo_audio/` with a filename beginning `consented-P-01-`.
The enforcement test matches on that prefix.

---

## 5. Revocation record

| ID | Date revoked | Artefacts deleted | Verified by | Date verified |
|---|---|---|---|---|
| _(none)_ | — | — | — | — |
