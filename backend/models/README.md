# backend/models/

Runtime model weights live here. Nothing in this directory is committed except this file — see
`.gitignore`.

## aasist-l.onnx

The official AASIST-L anti-spoofing model, exported to ONNX by the maintainer:

- Source: https://huggingface.co/SpeechAntiSpoofingBenchmarks/AASIST-L (MIT licence, inherited
  from the upstream [clovaai/aasist](https://github.com/clovaai/aasist) ASVspoof2019 LA checkpoint)
- Fetched automatically the first time the sidecar starts with `AASIST_ENABLED=true` (the
  default) and no file present at `AASIST_MODEL_PATH` (default: this directory), or on demand:

  ```
  python backend/tools/download_aasist_model.py
  ```

- The download is verified against a pinned SHA-256 (`backend/app/aasist.py:EXPECTED_SHA256`)
  before it is installed; a hash mismatch fails the download rather than loading an unverified
  binary. No huggingface_hub dependency is required — the fetch is a single HTTPS GET against the
  model's public `resolve/main` URL.
- Never re-downloaded automatically once present. Delete the file (or point `AASIST_MODEL_PATH`
  elsewhere) to force a fresh fetch.

See `docs/05-ML_MODEL_LIFECYCLE.md` for what this model is (and is not) validated for, and
`backend/app/aasist.py`'s module docstring for the exact input/output contract.
