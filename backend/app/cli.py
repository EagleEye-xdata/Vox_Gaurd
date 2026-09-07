"""Analyse a WAV file window by window and print the results as NDJSON.

A debugging aid for the Python half only: it exercises preprocessing, features, the detector and
speaker verification, and prints what the Go gateway would receive. It does not fuse, score a
session, or decide anything — that is the gateway's job.

    python -m app.cli ../demo_audio/fixture-steady.wav
"""
import argparse
import json
from pathlib import Path

from .sidecar import window_results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--identity", default=None, help="verify against this enrolled identity")
    args = parser.parse_args()
    for result in window_results(args.file, args.identity, 3.0, 1.0):
        # features are verbose and are what the gateway forwards to the dashboard, not a metric.
        result.pop("features", None)
        print(json.dumps(result))
