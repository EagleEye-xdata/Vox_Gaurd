# MVP verification — 6 September 2026

## Automated checks

- `python -m pytest tests -q`: **7 passed**. Two third-party TestClient deprecation warnings; no application failures.
- `npm run build`: **passed**, React/Vite production bundle generated.
- `git diff --check`: **passed**.
- Variable test signal CLI: 8/8 scored windows, finite pitch/jitter/shimmer features. One observed run took 972 ms for cold initialization and approximately 7–8 ms per subsequent window. These are observations from this machine, not latency guarantees.

## Browser checks

- Local backend connection, source picker, simulation start, live updates, completion.
- Steady test signal: 8 scored windows, sustained-risk alert, local escalation, verifiable ledger entries.
- Visible notification recommends callback/MFA and explicitly says the call has not been blocked.
- REAL/SYNTHETIC output is labelled heuristic. Speaker match remains unavailable until enrollment.
- Checked layouts at 320, 375, 414, and 768 CSS pixels, plus the default desktop viewport; no horizontal document overflow observed.
- Mobile navigation exposes monitor, pipeline, ledger, and guide.
- RMS waveform and risk-history chart render from actual derived results.

No genuine/cloned speech benchmark was supplied. These checks establish pipeline behavior only, not spoof-detection performance.
