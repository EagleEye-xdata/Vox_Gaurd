import argparse
import json
from pathlib import Path
from .ingestion import chunks
from .main import analyze

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    for index, audio in enumerate(chunks(args.file), 1):
        result = analyze(audio)
        print(json.dumps({"chunk": index, **result}))
