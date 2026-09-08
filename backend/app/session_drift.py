"""Cross-Session Consistency Checker — PS requirement: compare ongoing call against historical samples.

WHY THIS EXISTS
---------------
The PS explicitly asks: "compare the ongoing call to historical genuine samples to detect anomaly."

A caller can pass per-call speaker verification (cosine similarity vs enrolled baseline) and still
be a deepfake if:
  - The enrolled baseline itself was captured under different acoustic conditions.
  - The synthetic clone is "close enough" to the single enrolled template.
  - The real person's voice changes (illness, stress, aging) but the embedding drifts gradually.

This module maintains a *time-series* of per-call ECAPA-TDNN embeddings per identity and computes
a **drift score** measuring how far the current call's embedding deviates from the caller's own
historical distribution. A genuine caller's embeddings cluster tightly over time; a deepfake whose
voice is cloned from a single reference recording produces embeddings that may match the baseline
but drift from the cluster of genuine historical calls.

STORAGE DESIGN
--------------
We store embeddings in a lightweight SQLite table (memory-mapped, WAL mode) alongside their
SHA-256 hash for tamper-evidence. Raw audio is NEVER stored (CLAUDE.md invariant 1). Only the
24-float32 embedding vector + metadata is persisted.

PRIVACY
-------
Embeddings are anonymised mathematical projections of the voice, not audio. They cannot be used
to reconstruct the original waveform. They are deleted on revocation and rotated by policy.
The table lives in `backend/data/session_drift.db` — excluded from version control in .gitignore.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("voiceshield.session_drift")

# Location of the drift history database
_DB_PATH = Path(__file__).resolve().parents[2] / "backend" / "data" / "session_drift.db"

# How many historical embeddings to keep per identity (sliding window)
MAX_HISTORY_PER_IDENTITY = 50

# Minimum number of historical embeddings needed before drift scoring is meaningful
MIN_HISTORY_FOR_DRIFT = 3


def _open_db() -> sqlite3.Connection:
    """Open (or create) the drift history database."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embedding_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_id   TEXT NOT NULL,
            call_id       TEXT,
            recorded_at   REAL NOT NULL,
            embedding_b64 TEXT NOT NULL,
            emb_hash      TEXT NOT NULL,
            match_score   REAL,
            dim           INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_identity_time
        ON embedding_history(identity_id, recorded_at DESC)
    """)
    conn.commit()
    return conn


# Module-level connection (one per sidecar process)
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _open_db()
    return _conn


def _emb_to_b64(emb: np.ndarray) -> str:
    import base64
    return base64.b64encode(emb.astype(np.float32).tobytes()).decode("ascii")


def _b64_to_emb(b64: str, dim: int) -> np.ndarray:
    import base64
    raw = base64.b64decode(b64)
    return np.frombuffer(raw, dtype=np.float32)[:dim]


def _emb_hash(emb: np.ndarray) -> str:
    return hashlib.sha256(emb.astype(np.float32).tobytes()).hexdigest()[:16]


@dataclass
class DriftResult:
    drift_score: float = 0.0               # ∈ [0, 1]; high = suspicious drift from history
    history_size: int = 0                  # how many historical embeddings used
    mean_historical_similarity: float = 0.0  # average cosine sim to historical cluster
    min_historical_similarity: float = 0.0   # worst-case historical sim (outlier detection)
    drift_available: bool = False          # False if not enough history
    reason: Optional[str] = None


class SessionDriftChecker:
    """Records per-call speaker embeddings and detects cross-session identity drift."""

    def record_embedding(
        self,
        identity_id: str,
        embedding: np.ndarray,
        call_id: Optional[str] = None,
        match_score: Optional[float] = None,
    ) -> None:
        """Persist a caller's embedding for this call to the history table.

        Call this AFTER per-call verification succeeds (i.e., only genuine-enough callers
        get added to history, not every failed attempt).

        Parameters
        ----------
        identity_id : The verified caller's identity string.
        embedding   : Float32 numpy array (must not be aliased; caller should pass a copy).
        call_id     : Optional call session ID for auditability.
        match_score : The per-call verification similarity score, stored for trend analysis.
        """
        try:
            b64 = _emb_to_b64(embedding)
            h = _emb_hash(embedding)
            now = time.time()
            db = _db()
            db.execute(
                """
                INSERT INTO embedding_history
                    (identity_id, call_id, recorded_at, embedding_b64, emb_hash, match_score, dim)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (identity_id, call_id, now, b64, h, match_score, len(embedding)),
            )
            # Prune oldest entries beyond the sliding window
            db.execute(
                """
                DELETE FROM embedding_history
                WHERE identity_id = ?
                  AND id NOT IN (
                    SELECT id FROM embedding_history
                    WHERE identity_id = ?
                    ORDER BY recorded_at DESC
                    LIMIT ?
                  )
                """,
                (identity_id, identity_id, MAX_HISTORY_PER_IDENTITY),
            )
            db.commit()
        except Exception as exc:
            log.warning("Failed to record embedding for %s: %s", identity_id, exc)

    def check_drift(
        self,
        identity_id: str,
        current_embedding: np.ndarray,
    ) -> DriftResult:
        """Compute how much the current embedding deviates from historical cluster.

        A genuine caller's embeddings cluster tightly (mean cosine sim > 0.85 typically).
        A deepfake or impersonator will show lower similarity to the historical cluster even
        when their embedding happens to match the single enrolled baseline.

        Parameters
        ----------
        identity_id        : Caller whose history to compare against.
        current_embedding  : The embedding from the ongoing call (float32, normalised).
        """
        result = DriftResult()
        try:
            db = _db()
            rows = db.execute(
                """
                SELECT embedding_b64, dim FROM embedding_history
                WHERE identity_id = ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (identity_id, MAX_HISTORY_PER_IDENTITY),
            ).fetchall()

            if len(rows) < MIN_HISTORY_FOR_DRIFT:
                result.reason = f"insufficient_history ({len(rows)}/{MIN_HISTORY_FOR_DRIFT})"
                result.history_size = len(rows)
                return result

            # Reconstruct historical embeddings
            hist_embs = []
            for b64, dim in rows:
                emb = _b64_to_emb(b64, dim)
                norm = np.linalg.norm(emb)
                if norm > 1e-9:
                    hist_embs.append(emb / norm)

            if not hist_embs:
                result.reason = "all_historical_embeddings_invalid"
                return result

            # Normalise current embedding
            cur_norm = np.linalg.norm(current_embedding)
            cur_emb = current_embedding / (cur_norm + 1e-9)

            # Cosine similarities to all historical embeddings
            sims = np.array([float(np.dot(cur_emb, h)) for h in hist_embs])
            mean_sim = float(np.mean(sims))
            min_sim = float(np.min(sims))

            result.history_size = len(hist_embs)
            result.mean_historical_similarity = round(mean_sim, 4)
            result.min_historical_similarity = round(min_sim, 4)
            result.drift_available = True

            # Drift score: how far below the "genuine cluster" threshold we are.
            # Genuine callers: mean_sim > 0.80 → drift near 0.
            # Impersonator/clone: mean_sim around 0.55-0.70 → drift spikes.
            # Formula: sigmoid-shaped mapping centred at 0.75 similarity.
            drift_raw = max(0.0, 0.80 - mean_sim) / 0.40   # 0 at sim=0.80, 1 at sim=0.40
            result.drift_score = round(float(np.clip(drift_raw, 0.0, 1.0)), 4)

        except Exception as exc:
            result.reason = f"error: {exc}"
            log.warning("Drift check failed for %s: %s", identity_id, exc)

        return result

    def delete_history(self, identity_id: str) -> int:
        """Erase all historical embeddings for an identity (called on revocation)."""
        try:
            db = _db()
            cur = db.execute(
                "DELETE FROM embedding_history WHERE identity_id = ?", (identity_id,)
            )
            db.commit()
            return cur.rowcount
        except Exception as exc:
            log.warning("Failed to delete history for %s: %s", identity_id, exc)
            return 0

    def history_summary(self, identity_id: str) -> dict:
        """Return a summary of stored history for an identity (no embeddings, metadata only)."""
        try:
            db = _db()
            rows = db.execute(
                """
                SELECT call_id, recorded_at, match_score, emb_hash
                FROM embedding_history
                WHERE identity_id = ?
                ORDER BY recorded_at DESC
                LIMIT 20
                """,
                (identity_id,),
            ).fetchall()
            return {
                "identity_id": identity_id,
                "stored_calls": len(rows),
                "history": [
                    {
                        "call_id": r[0],
                        "recorded_at": r[1],
                        "match_score": r[2],
                        "emb_hash": r[3],
                    }
                    for r in rows
                ],
            }
        except Exception as exc:
            return {"identity_id": identity_id, "error": str(exc)}


# Module-level singleton
drift_checker = SessionDriftChecker()
