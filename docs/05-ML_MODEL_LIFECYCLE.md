# ML Model Lifecycle (v2)

> Fixes DR-015, DR-018, DR-023, DR-024, DR-028, DR-029.

## 1. Data sourcing

**Genuine speech** — openly licensed corpora plus enrolment samples given under explicit consent.
Never scraped. For a hackathon: public corpora plus your own team's recorded voices, with a signed
consent line per person. See `06-DATA_PRIVACY_COMPLIANCE.md` §0.

**Synthetic speech** — generate in-house across **multiple, diverse** TTS and voice-cloning
families. A detector trained on one generator learns that generator's artefacts, not synthesis in
general, and collapses against anything else. Record the generator identity, version, and settings
per sample so evaluation can break results down by family.

**Stratification** — language, accent, gender, age band, device/channel (studio · mobile mic ·
G.711 8 kHz · Opus · GSM-AMR), and SNR. Channel is the one teams forget and the one that dominates
real-world error: a detector trained on clean 16 kHz studio audio degrades sharply on 8 kHz
narrowband telephony, which is the only thing it will ever see in production.

**Versioning** — every training run pins an immutable dataset hash. Record it in the model card
and in `model_versions` on every decision.

## 2. Splits that test generalisation, not memorisation

Random splits inflate every metric here. Required:

- **Speaker-disjoint** — no speaker appears in both train and test.
- **Generator-disjoint** — hold out entire TTS/cloning families for the test set.
- **Channel-disjoint** — hold out at least one codec/device condition.
- **Language-disjoint** (where multilingual) — hold out at least one language to measure the
  out-of-scope penalty honestly.

Report each held-out axis separately. An aggregate number that averages over these hides the exact
failure mode that causes real harm.

## 3. Calibration — mandatory, not optional *(DR-018)*

`03-RISK_SCORING_SPEC.md` multiplies `p_synthetic` by a weight and treats the product as a risk
contribution. That is only meaningful if `p_synthetic` is a **calibrated** probability. Raw softmax
outputs from a modern network are systematically overconfident and are not probabilities.

- Fit a calibrator (temperature scaling first; isotonic if the reliability curve is non-monotonic)
  on a **held-out calibration split**, never on train and never on test.
- Ship the calibrator as its own versioned artefact: `calibrator_version` appears in
  `DetectionResult` and in every audit record.
- Gate: **Expected Calibration Error ≤ 0.05** with a reliability diagram in the model card.
- Re-fit the calibrator on every retrain and whenever channel mix shifts materially.

`confidence` is defined in `02-API_CONTRACTS.md` §3.1 as `1 − normalised predictive uncertainty`
from K forward passes (MC-dropout or a small ensemble, K=5). It is not a second copy of
`p_synthetic` and must not be implemented as `max(p, 1−p)`.

## 4. Language handling *(DR-023)*

Declare the supported-language set explicitly. Run language ID before detection.

- Supported → normal path.
- **Unsupported → `language_supported = false`.** The detector signal goes inactive and the
  degraded floor (40) applies. The model is not permitted to guess outside its evaluated scope.
- `und` (undetermined, e.g. too little speech) → treated as unsupported until enough audio arrives.

This matters disproportionately for an India-first deployment: a detector evaluated on English and
Hindi will behave unpredictably on Telugu, Tamil, Bengali, or heavy code-switching, which is the
normal register of Indian phone calls. Silence about this is the bug.

## 5. Diarisation *(DR-015)*

v1 had no speaker separation at all while listing Zoom/Teams meeting protection as a v1 vertical.
Required for any multi-party audio:
- Streaming diarisation with a bounded speaker count (`max_speakers`, default 6).
- Track-stable IDs across the session; re-labelling merges by embedding similarity.
- Per-track metrics: report diarisation error rate alongside detector metrics, because detector
  accuracy on a mis-attributed track is meaningless.

## 6. Adversarial robustness

Ongoing red-team function, not a launch checkbox.

- **Perturbation attacks** — crafted noise added to synthetic audio to flip the classifier. Measure
  the accuracy drop and confirm `adversarial_flag` fires. Test white-box and black-box variants.
- **Replay attacks** — a genuine recording of the real person played back at the verifier. Defence:
  channel/device fingerprint consistency, plus challenge-response for high-stakes step-up.
- **Compression laundering** — run every attack *through* a realistic phone codec. Attacks that
  die under G.711 are not real threats; attacks that survive it are the only ones that matter.
- **Adaptive attacker** — assume black-box query access via repeated calls. Rate-limit and detect
  probing at the gateway (`07-SECURITY_ARCHITECTURE.md` §3).
- **Attack corpus** — grows every time a bypass is found. Every model release regression-tests
  against the whole corpus. A release that regresses on any historical bypass does not ship.

## 7. Bias and fairness *(DR-028)*

Mandatory pre-launch and per-retrain report: error rates by accent, language, gender, age band, and
channel quality.

v1's threshold — "no subgroup FAR more than 1.5× overall FAR" — is statistically weak, because
"overall" is dominated by the largest subgroup and a ratio of two small numbers is unstable. v2:

- **Minimum subgroup n ≥ 200** samples, or the subgroup is reported as *unmeasured*, never as
  passing.
- Report **Wilson 95% confidence intervals** on every subgroup rate.
- Gate on the **worst-subgroup vs best-subgroup ratio**, not vs the overall mean:
  `FAR_worst / FAR_best ≤ 2.0` and `FRR_worst / FRR_best ≤ 2.0`, evaluated on CI **upper** bounds.
- A subgroup that cannot be measured blocks launch **for that subgroup's market**, not for the
  whole product. Say which markets are in scope.

For a hackathon, n ≥ 200 per subgroup is often impossible. Then the honest statement is: *"we
measured these three subgroups at n=250 and report the rest as unmeasured"* — which is a stronger
answer to a judge than a confident number over n=11.

## 8. Drift and retraining

- Monitor production distributions of `p_synthetic`, `confidence`, channel mix, and language mix.
  Alert on population-stability-index drift beyond threshold vs the training baseline.
- Retraining triggers: scheduled cadence · drift alert · a new generator family observed in the
  wild · FAR/FRR degradation reported by a pilot · any new entry in the attack corpus.
- **Rollback**: every deployed version stays instantly deployable. Because `model_versions` and
  `policy_version` are on every audit record, a bad release's blast radius is fully enumerable —
  you can list exactly which decisions it made.

## 9. Human-in-the-loop feedback

Every resolved alert (`CONFIRMED_FRAUD` / `FALSE_POSITIVE` / `INCONCLUSIVE`) becomes labelled data.

**Guard against the feedback loop**: analysts see the model's score before they label, so their
labels are correlated with the model's output. Training naively on them amplifies existing bias.
Mitigations: hold out a blind-labelled control sample where the reviewer does not see the score;
track inter-rater agreement; weight blind labels higher in retraining. *(DR-029)*

## 10. Model card — required per release

Training data summary and hash · supported languages and channels · **per-subgroup** FAR/FRR with
CIs · calibration curve and ECE · held-out generator families and results · adversarial regression
results · known limitations · intended use · **explicit out-of-scope uses** (e.g. "not validated
for evidentiary use", "not validated below 8 kHz", "not a lie detector").

The out-of-scope section is the one that protects you. Write it before someone else writes it for
you.
