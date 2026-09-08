/**
 * VoxGuard JavaScript/TypeScript SDK
 * PS requirement: "REST/gRPC APIs and SDKs" — integration-ready for banks and telecom platforms.
 *
 * Works in Node.js (using fetch, available since Node 18) and modern browsers.
 * Zero runtime dependencies — uses the native Fetch API only.
 *
 * @example
 * // Node.js / browser
 * import { VoxGuardClient } from './voxguard-sdk.js';
 *
 * const client = new VoxGuardClient({ baseUrl: 'http://localhost:8000' });
 *
 * // Analyse a file (Node.js)
 * const result = await client.analyseFile('./caller_audio.wav');
 * console.log(result.riskBand);       // "LOW" | "MEDIUM" | "HIGH"
 * console.log(result.shouldBlock);    // true if HIGH band
 * console.log(result.transcript);     // Whisper STT transcript
 *
 * // Quick boolean check
 * const isFake = await client.isSynthetic('./suspicious_audio.wav');
 *
 * // Compliance check before integration
 * const stmt = await client.complianceStatement();
 * console.log(stmt.inference_location); // "on_device"
 */

export class VoxGuardError extends Error {
  constructor(statusCode, message) {
    super(`VoxGuard API error ${statusCode}: ${message}`);
    this.statusCode = statusCode;
  }
}

/**
 * Per-window analysis result (3-second audio window).
 * @typedef {Object} WindowResult
 * @property {number} windowIndex
 * @property {boolean} scored
 * @property {number} pSynthetic      - AI-clone likelihood [0,1]
 * @property {number} prosodyRisk     - Behavioural/prosody risk [0,1]
 * @property {number} iRisk           - Intent/content risk [0,1]
 * @property {number|null} driftScore - Cross-session anomaly [0,1]
 * @property {number|null} matchScore - Speaker verification similarity
 * @property {string} transcript      - Whisper STT transcript for this window
 * @property {string[]} matchedPhrases - Vishing phrases detected
 * @property {string|null} languageDetected
 * @property {number} pitchMeanHz
 * @property {number} rhythmRegularity
 */

/**
 * Aggregated analysis result across all windows.
 * @typedef {Object} AnalysisResult
 * @property {number} pSynthetic
 * @property {number} prosodyRisk
 * @property {number} iRisk
 * @property {number|null} driftScore
 * @property {string} riskBand        - "LOW" | "MEDIUM" | "HIGH"
 * @property {string} policyDecision  - "ALLOW" | "WARN" | "STEP_UP" | "REJECT_AND_BLOCK"
 * @property {boolean} shouldBlock
 * @property {string} transcript
 * @property {string|null} languageDetected
 * @property {WindowResult[]} windows
 * @property {number} totalLatencyMs
 */

export class VoxGuardClient {
  /**
   * @param {Object} options
   * @param {string} [options.baseUrl="http://localhost:8000"]
   * @param {string} [options.apiKey]
   * @param {number} [options.timeoutMs=30000]
   */
  constructor({ baseUrl = 'http://localhost:8000', apiKey = null, timeoutMs = 30_000 } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.apiKey = apiKey;
    this.timeoutMs = timeoutMs;
  }

  _headers() {
    const h = { 'Accept': 'application/json' };
    if (this.apiKey) h['X-VoxGuard-Key'] = this.apiKey;
    return h;
  }

  async _fetch(path, options = {}) {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await fetch(url, {
        ...options,
        headers: { ...this._headers(), ...(options.headers || {}) },
        signal: controller.signal,
      });
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new VoxGuardError(res.status, text.slice(0, 300));
      }
      return res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  // ------------------------------------------------------------------ //
  // Core analysis                                                        //
  // ------------------------------------------------------------------ //

  /**
   * Analyse a WAV audio file (Node.js only — requires fs/blob API).
   * @param {string} filePath - Local path to WAV/MP3/OGG file.
   * @param {Object} [opts]
   * @param {string} [opts.identityId]
   * @param {number} [opts.windowSeconds=3.0]
   * @param {number} [opts.hopSeconds=1.5]
   * @returns {Promise<AnalysisResult>}
   */
  async analyseFile(filePath, { identityId, windowSeconds = 3.0, hopSeconds = 1.5 } = {}) {
    const started = Date.now();

    // Node.js: use fs to read the file
    let blob;
    if (typeof globalThis.Blob !== 'undefined' && typeof globalThis.fetch !== 'undefined') {
      // Try native fs first (Node 18+)
      try {
        const { readFile } = await import('fs/promises');
        const buf = await readFile(filePath);
        blob = new Blob([buf], { type: 'audio/wav' });
      } catch {
        throw new Error('analyseFile() requires Node.js 18+ with fs/promises');
      }
    } else {
      throw new Error('analyseFile() is only supported in Node.js environments');
    }

    const form = new FormData();
    form.append('file', blob, filePath.split(/[\\/]/).pop());

    const params = new URLSearchParams({ window_seconds: windowSeconds, hop_seconds: hopSeconds });
    if (identityId) params.set('identity_id', identityId);

    const raw = await this._fetch(`/api/v1/analyse?${params}`, { method: 'POST', body: form });
    return this._parseResponse(raw, identityId, Date.now() - started);
  }

  /**
   * Analyse raw PCM samples (works in browser and Node.js).
   * @param {Float32Array|number[]} samples - Float32 PCM at 16kHz.
   * @param {Object} [opts]
   * @param {number} [opts.sampleRate=16000]
   * @param {string} [opts.identityId]
   * @returns {Promise<AnalysisResult>}
   */
  async analyseSamples(samples, { sampleRate = 16000, identityId } = {}) {
    const started = Date.now();
    const body = JSON.stringify({
      samples: Array.from(samples),
      sample_rate: sampleRate,
      ...(identityId ? { identity_id: identityId } : {}),
    });
    const raw = await this._fetch('/api/v1/analyse/raw', {
      method: 'POST',
      body,
      headers: { 'Content-Type': 'application/json' },
    });
    return this._parseResponse(raw, identityId, Date.now() - started);
  }

  /**
   * @private Parse raw gateway response into typed AnalysisResult.
   */
  _parseResponse(raw, identityId, elapsedMs) {
    const windowsRaw = Array.isArray(raw.windows) ? raw.windows : [raw];
    const windows = windowsRaw.map((w, i) => {
      const intent = w.intent || {};
      const prosody = w.prosody || {};
      const verif = w.verification || {};
      const drift = w.session_drift || {};
      return {
        windowIndex: i + 1,
        scored: !!w.scored,
        reason: w.reason || null,
        pSynthetic: w.p_synthetic || 0,
        prosodyRisk: w.prosody_risk || 0,
        iRisk: w.i_risk || 0,
        driftScore: drift.drift_score ?? null,
        matchScore: verif.match_score ?? null,
        transcript: intent.transcript || '',
        matchedPhrases: intent.matched_phrases || [],
        languageDetected: intent.language_detected || null,
        pitchMeanHz: prosody.pitch_mean_hz || 0,
        rhythmRegularity: prosody.rhythm_regularity || 0,
        pauseCount: prosody.pause_count || 0,
        raw: w,
      };
    });

    const scored = windows.filter(w => w.scored);
    const pSynthetic = Math.max(0, ...scored.map(w => w.pSynthetic));
    const prosodyRisk = Math.max(0, ...scored.map(w => w.prosodyRisk));
    const iRisk = Math.max(0, ...scored.map(w => w.iRisk));
    const driftScores = scored.map(w => w.driftScore).filter(s => s !== null);
    const driftScore = driftScores.length ? Math.max(...driftScores) : null;

    const riskBand = raw.risk_band || raw.band || 'LOW';
    const policyDecision = raw.policy_decision || raw.decision || 'ALLOW';

    const transcripts = windows.map(w => w.transcript).filter(Boolean);
    const langs = windows.map(w => w.languageDetected).filter(Boolean);

    return {
      callId: raw.call_id || null,
      identityId,
      pSynthetic: +pSynthetic.toFixed(4),
      prosodyRisk: +prosodyRisk.toFixed(4),
      iRisk: +iRisk.toFixed(4),
      driftScore: driftScore !== null ? +driftScore.toFixed(4) : null,
      riskBand,
      policyDecision,
      shouldBlock: policyDecision === 'REJECT_AND_BLOCK',
      windows,
      transcript: transcripts.join(' '),
      languageDetected: langs[0] || null,
      totalLatencyMs: elapsedMs,
    };
  }

  // ------------------------------------------------------------------ //
  // Speaker management                                                  //
  // ------------------------------------------------------------------ //

  /** List enrolled speaker profiles. */
  async listSpeakers() {
    const data = await this._fetch('/api/v1/speakers');
    return data.speakers || [];
  }

  /** Revoke a speaker profile (DPDPA erasure right). */
  async revokeSpeaker(identityId) {
    return this._fetch(`/api/v1/speakers/${encodeURIComponent(identityId)}`, { method: 'DELETE' });
  }

  // ------------------------------------------------------------------ //
  // Compliance                                                          //
  // ------------------------------------------------------------------ //

  /**
   * Return the machine-readable on-device inference compliance statement.
   * Check this before integration to verify VoxGuard's data handling.
   * @returns {Promise<Object>}
   */
  async complianceStatement() {
    return this._fetch('/api/v1/compliance');
  }

  /** Check gateway health and active model versions. */
  async health() {
    return this._fetch('/api/v1/health');
  }

  // ------------------------------------------------------------------ //
  // Convenience                                                         //
  // ------------------------------------------------------------------ //

  /**
   * Quick boolean: is this audio likely AI-generated?
   * @param {string} filePath
   * @param {number} [threshold=0.65]
   * @returns {Promise<boolean>}
   */
  async isSynthetic(filePath, threshold = 0.65) {
    const result = await this.analyseFile(filePath);
    return result.pSynthetic >= threshold;
  }
}

// CommonJS compatibility shim
if (typeof module !== 'undefined') {
  module.exports = { VoxGuardClient, VoxGuardError };
}
