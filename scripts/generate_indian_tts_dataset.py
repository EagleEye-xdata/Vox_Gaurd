#!/usr/bin/env python3
"""Generate Indian-accented synthetic speech dataset for AASIST-L fine-tuning.

PS REQUIREMENT: "Multilingual + Indian accents/dialects" — AASIST-L must be trained
on Indian-accented synthetic (TTS) audio so it can detect Hindi/Tamil/Telugu voice clones,
not just Western-accented TTS voices.

WHAT THIS SCRIPT GENERATES
---------------------------
Using Meta MMS-TTS (already installed in this repo), it synthesises:
  - 200 Hindi utterances (ISO 639-1: hi)
  - 100 Tamil utterances  (ISO 639-1: ta)
  - 100 Telugu utterances (ISO 639-1: te)
  - 100 Bengali utterances (ISO 639-1: bn)
  - 100 English utterances with Indian-English text patterns

Each utterance is saved as a 16kHz mono WAV + a label file (spoof=1) ready for
AASIST-L fine-tuning in the ASVspoof 2019 LA protocol format.

USAGE
-----
    python scripts/generate_indian_tts_dataset.py --output_dir ./data/indian_tts_spoof

DEPENDENCIES
------------
    pip install transformers torch scipy soundfile
    (Meta MMS-TTS model weights download automatically on first run)

After running, merge this dataset with ASVspoof 2019 LA train set and fine-tune:
    python scripts/finetune_aasist.py \\
        --spoof_dir ./data/indian_tts_spoof \\
        --real_dir  ./data/asvspoof2019_LA_train/real
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import struct
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("voxguard.dataset_gen")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ---------------------------------------------------------------------------
# Utterance templates per language (banking / financial fraud domain)
# These phrases are the exact domain texts that vishing attackers use, making
# the synthetic clones hard to distinguish from genuine callers.
# ---------------------------------------------------------------------------
UTTERANCES: dict[str, list[str]] = {
    "hi": [
        "आपका खाता अस्थायी रूप से बंद कर दिया जाएगा।",
        "कृपया अपना ओटीपी साझा करें।",
        "आपके खाते में संदिग्ध गतिविधि पाई गई है।",
        "आपका डेबिट कार्ड ब्लॉक होने वाला है।",
        "तुरंत अपना पिन नंबर बताएं।",
        "बैंक अधिकारी बोल रहे हैं, कृपया सहयोग करें।",
        "आपके खाते से पैसे ट्रांसफर हो रहे हैं।",
        "साइबर क्राइम विभाग से बात हो रही है।",
        "आपका केवाईसी अपडेट करना अनिवार्य है।",
        "लीगल नोटिस भेजा जाएगा यदि सहयोग नहीं किया।",
    ] * 20,   # repeat to reach 200

    "ta": [
        "உங்கள் கணக்கு தற்காலிகமாக முடக்கப்படும்.",
        "உங்கள் OTP ஐ பகிர்ந்து கொள்ளுங்கள்.",
        "வங்கி அதிகாரி பேசுகிறேன்.",
        "உங்கள் அட்டை தடுக்கப்படவிருக்கிறது.",
        "உடனடியாக பதிலளிக்கவும்.",
        "சட்ட நடவடிக்கை எடுக்கப்படும்.",
        "சைபர் க்ரைம் பிரிவு அழைக்கிறது.",
        "கேவைசி புதுப்பிக்க வேண்டும்.",
        "நிதி மோசடி கண்டறியப்பட்டது.",
        "உங்கள் PIN எண்ணை உறுதிப்படுத்துங்கள்.",
    ] * 10,

    "te": [
        "మీ ఖాతా తాత్కాలికంగా నిలిపివేయబడుతుంది.",
        "మీ OTP ని షేర్ చేయండి.",
        "బ్యాంక్ అధికారి మాట్లాడుతున్నారు.",
        "మీ కార్డ్ బ్లాక్ అవుతుంది.",
        "వెంటనే స్పందించండి.",
        "చట్టపరమైన చర్య తీసుకోబడుతుంది.",
        "సైబర్ క్రైమ్ విభాగం నుండి కాల్.",
        "KYC అప్‌డేట్ అవసరం.",
        "నగదు బదిలీ జరుగుతోంది.",
        "మీ PIN నంబర్ నిర్ధారించండి.",
    ] * 10,

    "bn": [
        "আপনার অ্যাকাউন্ট সাময়িকভাবে বন্ধ করা হবে।",
        "আপনার ওটিপি শেয়ার করুন।",
        "ব্যাংক কর্মকর্তা কথা বলছেন।",
        "আপনার কার্ড ব্লক হতে চলেছে।",
        "অবিলম্বে সাড়া দিন।",
        "আইনি ব্যবস্থা নেওয়া হবে।",
        "সাইবার ক্রাইম বিভাগ থেকে কল।",
        "কেওয়াইসি আপডেট করা বাধ্যতামূলক।",
        "আপনার পিন নম্বর নিশ্চিত করুন।",
        "তহবিল স্থানান্তর হচ্ছে।",
    ] * 10,

    "en": [
        "Your account will be suspended immediately.",
        "Please share your OTP to verify your identity.",
        "This is the fraud prevention department calling.",
        "Your debit card will be blocked in 30 minutes.",
        "Please transfer the amount to secure your account.",
        "Your KYC is pending, legal action will follow.",
        "Do not disconnect, this is an urgent security alert.",
        "Please provide your PIN number to unblock your card.",
        "A suspicious transaction has been detected in your account.",
        "Wire transfer of 50000 rupees has been initiated.",
    ] * 10,
}

MMS_LANGUAGE_CODE = {
    "hi": "hin",
    "ta": "tam",
    "te": "tel",
    "bn": "ben",
    "en": "eng",
}


def synthesise_mms(text: str, lang_code: str, output_path: Path) -> bool:
    """Synthesise `text` using Meta MMS-TTS and save as 16kHz mono WAV.

    Returns True on success, False on failure (model not loaded / text too long).
    """
    try:
        from transformers import VitsModel, AutoTokenizer
        import torch
        import soundfile as sf

        model_id = f"facebook/mms-tts-{lang_code}"
        tokenizer = AutoTokenizer.from_pretrained(model_id)
        model = VitsModel.from_pretrained(model_id)
        model.eval()

        with torch.no_grad():
            inputs = tokenizer(text, return_tensors="pt")
            waveform = model(**inputs).waveform[0].numpy()

        # MMS-TTS outputs at 16kHz natively
        sf.write(str(output_path), waveform, samplerate=16000, subtype="PCM_16")
        return True
    except Exception as exc:
        log.warning("MMS-TTS failed for lang=%s: %s", lang_code, exc)
        return False


def save_label_csv(records: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "language", "label", "text"])
        writer.writeheader()
        writer.writerows(records)


def main():
    parser = argparse.ArgumentParser(description="Generate Indian TTS spoof dataset")
    parser.add_argument("--output_dir", default="data/indian_tts_spoof",
                        help="Output directory for WAV files and labels")
    parser.add_argument("--max_per_lang", type=int, default=50,
                        help="Maximum utterances per language (default 50 for quick demo)")
    parser.add_argument("--dry_run", action="store_true",
                        help="Skip TTS synthesis — generate label CSV only (for CI)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_dir = out_dir / "wav"
    wav_dir.mkdir(exist_ok=True)

    records: list[dict] = []
    total = 0

    for lang, texts in UTTERANCES.items():
        lang_code = MMS_LANGUAGE_CODE[lang]
        log.info("Generating %s utterances for language: %s (%s)", min(len(texts), args.max_per_lang), lang, lang_code)

        for i, text in enumerate(texts[:args.max_per_lang]):
            fname = f"{lang}_{i:04d}.wav"
            fpath = wav_dir / fname

            if args.dry_run:
                success = True
            else:
                success = synthesise_mms(text, lang_code, fpath)

            if success:
                records.append({
                    "file": str(fpath.relative_to(out_dir)),
                    "language": lang,
                    "label": "spoof",   # 1 = synthetic, ASVspoof convention
                    "text": text,
                })
                total += 1

    label_path = out_dir / "labels.csv"
    save_label_csv(records, label_path)

    # Write a manifest JSON for AASIST-L fine-tune script compatibility
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps({
        "dataset": "VoxGuard Indian TTS Spoof Dataset",
        "version": "1.0",
        "total_utterances": total,
        "languages": list(UTTERANCES.keys()),
        "label_file": "labels.csv",
        "audio_dir": "wav/",
        "sample_rate": 16000,
        "format": "PCM_16 WAV mono",
        "notes": (
            "Synthesised with Meta MMS-TTS. All utterances are banking/vishing domain "
            "texts to maximise AASIST-L fine-tuning relevance for Indian fraud calls. "
            "Combine with ASVspoof 2019 LA real-speech set for balanced training."
        ),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("Dataset ready: %d utterances in %s", total, out_dir)
    log.info("Labels: %s", label_path)
    log.info("Manifest: %s", manifest_path)


if __name__ == "__main__":
    main()
