"""Fetch the official AASIST-L ONNX model file once, ahead of time.

    python backend/tools/download_aasist_model.py

Not required: `AASISTLClassifier.load()` (backend/app/aasist.py) calls the same
`download_model()` helper automatically on first use when the file is missing and
`AASIST_AUTO_DOWNLOAD` is not disabled. This script exists for setup scripts, CI pre-warming, or
running the fetch once by hand ahead of an offline demo, so ordinary inference never depends on
network access after the first successful run.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.aasist import DEFAULT_MODEL_PATH, EXPECTED_SHA256, MODEL_URL, download_model  # noqa: E402


def main() -> int:
    if DEFAULT_MODEL_PATH.exists():
        print(f"Already present: {DEFAULT_MODEL_PATH}")
        return 0
    print(f"Downloading AASIST-L from {MODEL_URL}")
    print(f"  -> {DEFAULT_MODEL_PATH}")
    ok = download_model(DEFAULT_MODEL_PATH, MODEL_URL, EXPECTED_SHA256)
    if not ok:
        print("Download failed or hash mismatch. See log output above. The sidecar will fall "
              "back to the heuristic detector until this succeeds.", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
