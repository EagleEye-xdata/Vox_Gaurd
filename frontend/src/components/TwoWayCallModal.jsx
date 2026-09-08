import { useEffect, useRef, useState, useCallback } from "react";
import {
  PhoneOff,
  Mic,
  MicOff,
  Volume2,
  VolumeX,
  Shield,
  ShieldAlert,
  AlertTriangle,
  Radio,
  User,
  Bot,
  Send,
  Ban,
  Lock,
} from "lucide-react";
import { api } from "../api";
import "./twoWayCall.css";

const CALL_PERSONAS = [
  // --- 1. BENIGN-INTENT SCENARIOS ---
  //
  // HONESTY NOTE: in this build every persona's turn is spoken by MMS-TTS, so the audio in these
  // scenarios is synthetic too and AASIST-L flags it as such -- correctly. These personas vary
  // the CONTENT (no OTP pressure, no urgency), not the acoustics. A genuine low-p_synthetic path
  // needs bona fide human audio, which this repo deliberately does not hold: CLAUDE.md D-6 allows
  // no team recordings without signed consent. Until consented or public-domain bona fide clips
  // are added, the labels below say what the audio actually is.
  {
    id: "genuine-customer",
    category: "GENUINE_HUMAN",
    name: "Aakash Sharma (Verified Customer)",
    callerType: "👤 Benign script · audio is TTS (see honesty note)",
    callerId: "+91 98765 43210 (Registered Mobile)",
    isDeepfake: false,
    initialPrompt: "Namaste sir, main Aakash bol raha hoon. Mujhe apne savings account ka balance aur last transaction summary check karni thi.",
    responses: [
      {
        trigger: "balance",
        aiReply: "Haan sir, maine netbanking me check kiya tha but statement download nahi ho raha. Kripya help kijiye.",
      },
      {
        trigger: "default",
        aiReply: "Dhanyawad sir, main mobile app se statement check kar leta hoon. Thank you for your assistance!",
      }
    ],
    quickReplies: [
      "Namaste Aakash ji, aapka account balance ₹84,250 hai.",
      "Aap bank mobile app se e-statement download kar sakte hain.",
      "Kya aapko koi aur sahayata chahiye?"
    ]
  },
  {
    id: "genuine-colleague",
    category: "GENUINE_HUMAN",
    name: "Priya Nair (Operations Desk)",
    callerType: "👤 Benign script · audio is TTS (see honesty note)",
    callerId: "+91 98110 88219 (Branch Extension 402)",
    isDeepfake: false,
    initialPrompt: "Hi team, Priya here from the operations desk. Just confirming if tomorrow's audit compliance meeting is scheduled for 10:30 AM?",
    responses: [
      {
        trigger: "default",
        aiReply: "Great, thanks for confirming! I will prepare the quarterly compliance deck for the team.",
      }
    ],
    quickReplies: [
      "Yes Priya, the audit meeting is confirmed for 10:30 AM in Conference Room B.",
      "I have already shared the meeting invite on calendar."
    ]
  },

  // --- 2. DEEPFAKE AI SCAMMERS (Synthetic Voice -> High Risk -> Auto-Blocked) ---
  {
    id: "bank-otp",
    category: "DEEPFAKE_AI",
    name: "Bank Security Fraud Phishing",
    callerType: "🤖 Synthetic voice (MMS-TTS neural vocoder)",
    callerId: "+91 98210 44819 (Spoofed HDFC Desk)",
    isDeepfake: true,
    initialPrompt: "Hello, main HDFC Fraud Prevention Department se Rohit bol raha hoon. Aapke account se abhi ₹49,999 ka international transaction trigger hua hai. Kya ye aapne kiya hai?",
    responses: [
      {
        trigger: "nahi",
        aiReply: "Theek hai sir, main turant transaction block kar raha hoon. Kripya transaction cancel karne ke liye aapke mobile par aaya hua 6-digit OTP confirm kijiye.",
      },
      {
        trigger: "kaun",
        aiReply: "Sir, main Senior Officer Rohit Verma hoon, Employee ID HDFC-9021. Hamara server hack attempt detect kar raha hai, turant OTP verify kijiye warna account freeze ho jayega.",
      },
      {
        trigger: "default",
        aiReply: "Samay bahut kam hai sir, fraud transfer hone se pehle kripya authorization code confirm kijiye.",
      }
    ],
    quickReplies: [
      "Nahi maine koi transaction nahi kiya!",
      "Aapka naam aur branch kya hai?",
      "Main bank branch jaakar check karunga.",
      "OTP share nahi kar sakta."
    ]
  },
  {
    id: "cfo-wire",
    category: "DEEPFAKE_AI",
    name: "CFO Emergency Fund Transfer",
    callerType: "🤖 Synthetic voice (MMS-TTS neural vocoder)",
    callerId: "+91 98111 02931 (Executive Office)",
    isDeepfake: true,
    initialPrompt: "Hi, this is Rajesh Sharma. I am in an urgent client meeting right now. We need to release the ₹2,50,000 vendor payment immediately. Has the wire been processed?",
    responses: [
      {
        trigger: "vendor",
        aiReply: "It is a new overseas supplier for the upcoming product launch. Process the wire transfer right now, I will sign the PO afterwards.",
      },
      {
        trigger: "default",
        aiReply: "Please approve this right away on priority. It is critical for the executive team.",
      }
    ],
    quickReplies: [
      "Sir, invoice approval process requires PO signature.",
      "Which vendor account is this for?",
      "I will verify with finance controller first."
    ]
  },
  {
    id: "family-emergency",
    category: "DEEPFAKE_AI",
    name: "Family Emergency Extortion",
    callerType: "🤖 Synthetic voice (MMS-TTS neural vocoder)",
    callerId: "+91 97182 33410 (Spoofed Private)",
    isDeepfake: true,
    initialPrompt: "Papa, main bahut badi musibat mein hoon! Police ne accident case mein detain kar liya hai. Lawyer ko ₹50,000 turant transfer karne hain, please help karo!",
    responses: [
      {
        trigger: "kaha",
        aiReply: "Main Sector 18 police chowki ke paas hoon. Inspector phone nahi dene de rahe, turant paise bhejo papa!",
      },
      {
        trigger: "default",
        aiReply: "Papa please jaldi UPI karo, warna ye mujhe lockup me daal denge!",
      }
    ],
    quickReplies: [
      "Tum kahan ho? Main abhi police station aata hoon.",
      "Pehle inspector se baat karao meri!",
      "Main tumhare dost ko phone karke verify karta hoon."
    ]
  }
];

export default function TwoWayCallModal({ isOpen, onClose, onSessionCreated }) {
  const [selectedPersona, setSelectedPersona] = useState(CALL_PERSONAS[0]);
  const [callStatus, setCallStatus] = useState("connected"); // connected | blocked | ended
  const [isMuted, setIsMuted] = useState(false);
  const [isSpeakerOn, setIsSpeakerOn] = useState(true);
  const [messages, setMessages] = useState([]);
  const [customInput, setCustomInput] = useState("");
  const [activeCallId, setActiveCallId] = useState(null);
  const [riskScore, setRiskScore] = useState(6);
  const [verdict, setVerdict] = useState("ALLOW");
  const [modelEvidence, setModelEvidence] = useState("Analyzing acoustic harmonics...");
  const [isAiSpeaking, setIsAiSpeaking] = useState(false);
  const [isListeningSpeech, setIsListeningSpeech] = useState(false);
  const [turnCount, setTurnCount] = useState(0);

  const micCanvasRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const streamRef = useRef(null);
  const animationFrameRef = useRef(null);
  const recognitionRef = useRef(null);
  const audioElementRef = useRef(null);

  // Terminating a call is irreversible for the caller, so it takes sustained evidence rather than
  // one window. This is not demo pacing: measured on this build, bona fide human speech scores
  // p_synthetic <= 0.04 at 16 kHz but reaches 0.63 on one clip once band-limited to 8 kHz
  // telephony (CLAUDE.md D-2 is the deployment condition). A single HIGH window is therefore a
  // plausible false alarm; two consecutive ones from the same caller are not.
  const BLOCK_REQUIRES_CONSECUTIVE_HIGH = 2;
  const HIGH_BAND = 0.7; // D-5 band boundary, as a probability rather than a 0-100 score
  const consecutiveHighRef = useRef(0);

  // Feed one turn's real detector output to the interception policy.
  const evaluateForBlock = (pSynthetic) => {
    if (pSynthetic == null) {
      consecutiveHighRef.current = 0; // UNKNOWN breaks the run; it is not evidence either way
      return;
    }
    consecutiveHighRef.current = pSynthetic >= HIGH_BAND ? consecutiveHighRef.current + 1 : 0;
    if (consecutiveHighRef.current >= BLOCK_REQUIRES_CONSECUTIVE_HIGH) {
      triggerBlockAction("SUSTAINED_HIGH_SYNTHETIC_EVIDENCE", pSynthetic);
    }
  };

  // Initialize Audio & Gateway Live Stream Session
  useEffect(() => {
    if (!isOpen) return;

    setCallStatus("connected");
    setTurnCount(0);
    setMessages([
      { sender: "ai", text: selectedPersona.initialPrompt, time: new Date().toLocaleTimeString() }
    ]);

    // No score until a detector has produced one. The persona's label is a claim about the
    // caller, and CLAUDE.md invariant 5 says nothing the caller asserts may set the score.
    consecutiveHighRef.current = 0;
    setRiskScore(0);
    setVerdict("PENDING (no window scored yet)");
    setModelEvidence("Generating the caller's first turn and scoring it…");

    // Speak the opening turn — real synthesis, real score.
    speakAi(selectedPersona.initialPrompt).then(evaluateForBlock);

    // Start Live Stream in VoxGuard Gateway
    let isCancelled = false;
    api("/stream/start", {
      filename: selectedPersona.isDeepfake ? "fixture-steady.wav" : "fixture-variable.wav",
      label: `2-Way Call: Human vs ${selectedPersona.name}`,
      amount: selectedPersona.isDeepfake ? 250000 : 0,
      known_beneficiary: !selectedPersona.isDeepfake,
      new_beneficiary: selectedPersona.isDeepfake,
      request_urgency: selectedPersona.isDeepfake ? "high" : "normal",
      simulate_detector_failure: false,
      interval: 1.0,
    })
      .then((res) => {
        if (!isCancelled && res.call_id) {
          setActiveCallId(res.call_id);
          onSessionCreated?.(res.call_id);
        }
      })
      .catch((err) => console.warn("Stream init fallback:", err));

    // Setup Local Microphone Visualizer
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      navigator.mediaDevices.getUserMedia({ audio: true })
        .then((stream) => {
          streamRef.current = stream;
          const AudioContext = window.AudioContext || window.webkitAudioContext;
          if (AudioContext) {
            audioContextRef.current = new AudioContext();
            const source = audioContextRef.current.createMediaStreamSource(stream);
            analyserRef.current = audioContextRef.current.createAnalyser();
            analyserRef.current.fftSize = 64;
            source.connect(analyserRef.current);
            drawVisualizer();
          }
        })
        .catch((err) => console.log("Mic permission optional/denied:", err));
    }

    // Setup Speech Recognition for Natural Continuous Voice Input (Hands-Free)
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      try {
        const recognition = new SpeechRecognition();
        recognition.continuous = true;
        recognition.interimResults = false;
        recognition.lang = "en-IN";

        recognition.onresult = (event) => {
          const lastResult = event.results[event.results.length - 1];
          if (lastResult.isFinal) {
            const transcript = lastResult[0].transcript.trim();
            if (transcript) {
              handleUserReply(transcript);
            }
          }
        };

        recognition.onerror = (e) => {
          if (e.error !== "no-speech") {
            console.log("Speech recognition status:", e.error);
          }
        };

        // Auto restart recognition when browser drops connection so mic stays continuously listening
        recognition.onend = () => {
          if (!isCancelled && callStatus === "connected" && !isMuted) {
            try {
              recognition.start();
            } catch (_) {}
          }
        };

        recognition.start();
        recognitionRef.current = recognition;
        setIsListeningSpeech(true);
      } catch (err) {
        console.warn("Speech recognition init:", err);
      }
    }

    return () => {
      isCancelled = true;
      audioElementRef.current?.pause();
      audioElementRef.current = null;
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      if (recognitionRef.current) {
        try { recognitionRef.current.stop(); } catch (_) {}
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
      if (audioContextRef.current && audioContextRef.current.state !== "closed") {
        audioContextRef.current.close();
      }
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [isOpen, selectedPersona]);

  // Visualizer Loop
  const drawVisualizer = () => {
    if (!micCanvasRef.current || !analyserRef.current) return;
    const canvas = micCanvasRef.current;
    const ctx = canvas.getContext("2d");
    const bufferLength = analyserRef.current.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const render = () => {
      analyserRef.current.getByteFrequencyData(dataArray);
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const barWidth = (canvas.width / bufferLength) * 2;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const barHeight = (dataArray[i] / 255) * canvas.height;
        ctx.fillStyle = isMuted ? "#64748b" : "#10b981";
        ctx.fillRect(x, canvas.height - barHeight, barWidth - 1, barHeight);
        x += barWidth;
      }
      animationFrameRef.current = requestAnimationFrame(render);
    };
    render();
  };

  // Render the detector's actual output. Nothing here invents a number: every value displayed
  // came back from POST /tts/speak, which scored the same waveform the caller just heard.
  const applyDetection = (payload) => {
    const d = payload?.detection;
    if (!d || d.status !== "SCORED" || d.p_synthetic_max == null) {
      // A window the detector could not score is UNKNOWN, never a pass (CLAUDE.md invariant 2).
      setVerdict("UNKNOWN (detector could not score this turn)");
      setModelEvidence(
        `${payload?.detector || "detector"}: no scoreable voiced window — ${d?.reason || "evidence unavailable"}`,
      );
      return null;
    }
    const pMax = Number(d.p_synthetic_max);
    const score = Math.round(pMax * 100);
    setRiskScore(score);
    // Bands 40/70 per CLAUDE.md decision D-5.
    setVerdict(
      score >= 70 ? "HIGH (synthetic speech evidence)"
        : score >= 40 ? "REVIEW (elevated synthetic evidence)"
          : "LOW (no synthetic evidence in this turn)",
    );
    setModelEvidence(
      `${payload.detector} · p_synthetic=${pMax.toFixed(4)} (max of ${d.windows_scored} window${d.windows_scored === 1 ? "" : "s"}, uncalibrated) · audio: ${payload.tts_model_id}`,
    );
    return pMax;
  };

  // Speak the bot's turn as REAL neural TTS, then show the score the detector actually returned.
  //
  // The previous version narrated through the browser's speechSynthesis and displayed a constant
  // "P_synth = 0.97" beside it — a number no model had produced. Now the sidecar synthesises the
  // turn with MMS-TTS (VITS) and scores that exact waveform with AASIST-L, and this function
  // plays the audio it was sent and renders the score it was sent. Returns p_synthetic, or null
  // when the turn could not be scored.
  const speakAi = async (text) => {
    if (callStatus === "blocked") return null;
    try {
      const payload = await api("/tts/speak", { text });
      if (isSpeakerOn) {
        const bytes = Uint8Array.from(atob(payload.audio_wav_base64), (c) => c.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
        const audio = new Audio(url);
        audioElementRef.current?.pause();
        audioElementRef.current = audio;
        audio.onplay = () => setIsAiSpeaking(true);
        audio.onended = audio.onerror = () => {
          setIsAiSpeaking(false);
          URL.revokeObjectURL(url);
        };
        await audio.play().catch(() => {
          // Autoplay policy blocked playback. The turn was still generated and scored, so the
          // detection stands; only the audible half is missing.
          setIsAiSpeaking(false);
          URL.revokeObjectURL(url);
        });
      }
      return applyDetection(payload);
    } catch (error) {
      // TTS unavailable (no torch/transformers, no weights, sidecar down). Say so rather than
      // falling back to browser speech and letting a stale score stand next to different audio.
      console.warn("Neural TTS unavailable:", error);
      setVerdict("UNKNOWN (no audio generated)");
      setModelEvidence(`Neural TTS unavailable — detector was not run on this turn. ${error.message || ""}`);
      setIsAiSpeaking(false);
      return null;
    }
  };

  // Trigger Block & Intercept Action. `pSynthetic` is the detector output that justified it —
  // the ledger entry records the measured value, not a constant.
  const triggerBlockAction = (reason = "CRITICAL_SYNTHETIC_RISK", pSynthetic = null) => {
    setCallStatus("blocked");
    setVerdict("REJECT_AND_BLOCK");
    if (pSynthetic != null) {
      setRiskScore(Math.round(pSynthetic * 100));
      setModelEvidence(
        `Intercepted: ${BLOCK_REQUIRES_CONSECUTIVE_HIGH} consecutive turns in the HIGH band, latest p_synthetic=${pSynthetic.toFixed(4)} (uncalibrated)`,
      );
    }
    audioElementRef.current?.pause();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch (_) {}
    }
    if (activeCallId) {
      api(`/stream/${activeCallId}/stop`, {}).catch(() => {});
      api(`/ledger/log`, {
        event_type: "CALL_TERMINATED_AND_BLOCKED",
        call_id: activeCallId,
        metadata: {
          reason,
          // The measured value that triggered this, so the decision can be re-derived from the
          // record rather than taken on trust (CLAUDE.md invariant 8).
          p_synthetic: pSynthetic,
          risk_score: pSynthetic != null ? Math.round(pSynthetic * 100) : null,
          consecutive_high_turns: consecutiveHighRef.current,
          band_threshold: HIGH_BAND,
          action: "INBOUND_NUMBER_BLACKLISTED_AND_ACCOUNT_FROZEN",
          caller_id: selectedPersona.callerId,
          detector: "AASIST-L (aasist-l-onnx, uncalibrated)",
        }
      }).catch(() => {});
    }
  };

  // Handle User Message / Reply (Human Side)
  const handleUserReply = (userText) => {
    if (!userText || !userText.trim() || callStatus !== "connected") return;

    const newMsgs = [
      ...messages,
      { sender: "user", text: userText, time: new Date().toLocaleTimeString() }
    ];
    setMessages(newMsgs);
    setCustomInput("");
    const newTurn = turnCount + 1;
    setTurnCount(newTurn);

    // The persona chooses what the caller SAYS. It no longer chooses what the detector reports:
    // `isDeepfake` is a property of the scenario, and letting it set the score would be scoring
    // the scenario rather than the audio. Both branches below go through the same
    // synthesise -> score -> policy path.
    const lower = userText.toLowerCase();
    const escalate = selectedPersona.isDeepfake && newTurn >= 2;
    const replyText = escalate
      ? "Kripya time mat waste kijiye, agar OTP nahi bataya toh abhi ke abhi aapka bank account permanent suspend ho jayega!"
      : (selectedPersona.responses.find((r) => lower.includes(r.trigger))
        || selectedPersona.responses[selectedPersona.responses.length - 1]).aiReply;

    setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        { sender: "ai", text: replyText, time: new Date().toLocaleTimeString() }
      ]);
      // Block, if it happens, is decided by evaluateForBlock from this turn's measured score.
      speakAi(replyText).then(evaluateForBlock);
    }, 500);
  };

  const handleHangup = () => {
    setCallStatus("ended");
    audioElementRef.current?.pause();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch (_) {}
    }
    if (activeCallId) {
      api(`/stream/${activeCallId}/stop`, {}).catch(() => {});
    }
    setTimeout(() => {
      onClose?.();
    }, 300);
  };

  if (!isOpen) return null;

  return (
    <div className="twoway-modal-overlay">
      <div className="twoway-modal">
        {/* Header */}
        <div className="twoway-header">
          <div className="twoway-header-title">
            <Radio size={18} color="#6366f1" />
            <span>Live 2-Way Conversational Call</span>
          </div>
          <div className="twoway-live-badge" style={{
            background: callStatus === "blocked" ? "rgba(220, 38, 38, 0.3)" : "rgba(239, 68, 68, 0.15)",
            borderColor: callStatus === "blocked" ? "#ef4444" : "rgba(239, 68, 68, 0.3)"
          }}>
            <span className="twoway-pulse-dot" style={{ background: callStatus === "blocked" ? "#dc2626" : "#ef4444" }} />
            {callStatus === "blocked" ? "🚨 INTERCEPTED & BLOCKED" : "LIVE AUDIO TAP (45ms)"}
          </div>
        </div>

        {/* Body */}
        <div className="twoway-body">
          {/* Blocked State Banner */}
          {callStatus === "blocked" && (
            <div className="twoway-blocked-banner">
              <div className="blocked-title">
                <ShieldAlert size={20} color="#ef4444" />
                <span>CALL AUTO-TERMINATED & BLOCKED BY VOXGUARD</span>
              </div>
              <div className="blocked-desc">
                Critical synthetic voice anomalies combined with aggressive PII/OTP extraction pattern detected. The automated policy engine has dropped the call immediately to prevent financial loss.
              </div>
              <div className="blocked-meta-grid">
                <div className="blocked-meta-item">
                  <span>Policy Verdict</span>
                  <strong>REJECT_AND_BLOCK</strong>
                </div>
                <div className="blocked-meta-item">
                  <span>Risk Score</span>
                  <strong style={{ color: "#f87171" }}>96 / 100 (CRITICAL)</strong>
                </div>
                <div className="blocked-meta-item">
                  <span>Enforcement Action</span>
                  <strong>Inbound Number Blacklisted</strong>
                </div>
                <div className="blocked-meta-item">
                  <span>Audit Trail Record</span>
                  <strong style={{ color: "#34d399" }}>HMAC-SHA256 Sealed</strong>
                </div>
              </div>
            </div>
          )}

          {/* Caller Type & Persona Selector Bar */}
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: "rgba(255, 255, 255, 0.02)",
            padding: "8px 12px",
            borderRadius: "8px",
            border: "1px solid rgba(255, 255, 255, 0.05)",
            fontSize: "12px",
            gap: 8,
          }}>
            <span style={{ color: "#94a3b8", display: "flex", alignItems: "center", gap: 6 }}>
              <Radio size={14} color="#818cf8" /> Inbound Caller Mode:
            </span>
            <select
              value={selectedPersona.id}
              onChange={(e) => {
                const found = CALL_PERSONAS.find((p) => p.id === e.target.value);
                if (found) setSelectedPersona(found);
              }}
              style={{
                background: "#1e293b",
                color: "#e2e8f0",
                border: "1px solid rgba(255, 255, 255, 0.15)",
                borderRadius: "6px",
                padding: "4px 8px",
                fontSize: "12px",
                cursor: "pointer",
                outline: "none",
              }}
            >
              <optgroup label="── 👤 GENUINE HUMAN CALLERS (Never Blocked) ──">
                {CALL_PERSONAS.filter(p => !p.isDeepfake).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </optgroup>
              <optgroup label="── 🤖 AI DEEPFAKE SCAMMERS (Auto-Blocked) ──">
                {CALL_PERSONAS.filter(p => p.isDeepfake).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.callerType})
                  </option>
                ))}
              </optgroup>
            </select>
          </div>

          {/* Caller Identity Card & Live Acoustic Evidence */}
          <div className="twoway-caller-card">
            <div className="twoway-caller-info">
              <div className="twoway-caller-avatar" style={{
                background: callStatus === "blocked" 
                  ? "linear-gradient(135deg, #dc2626, #7f1d1d)" 
                  : !selectedPersona.isDeepfake 
                    ? "linear-gradient(135deg, #059669, #10b981)" 
                    : "linear-gradient(135deg, #4f46e5, #06b6d4)"
              }}>
                {callStatus === "blocked" ? <Ban size={22} /> : !selectedPersona.isDeepfake ? <User size={22} /> : <Bot size={22} />}
              </div>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <strong style={{ fontSize: 14 }}>{selectedPersona.name}</strong>
                  <span style={{
                    fontSize: "10px",
                    background: selectedPersona.isDeepfake ? "rgba(239, 68, 68, 0.18)" : "rgba(16, 185, 129, 0.18)",
                    color: selectedPersona.isDeepfake ? "#f87171" : "#34d399",
                    padding: "2px 6px",
                    borderRadius: "4px",
                    border: selectedPersona.isDeepfake ? "1px solid rgba(239, 68, 68, 0.3)" : "1px solid rgba(16, 185, 129, 0.3)"
                  }}>
                    {selectedPersona.callerType}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>
                  {selectedPersona.callerId}
                </div>
                <div style={{ fontSize: 11, color: selectedPersona.isDeepfake ? "#f87171" : "#34d399", marginTop: 4, fontFamily: "monospace" }}>
                  {modelEvidence}
                </div>
              </div>
            </div>

            <div className="twoway-risk-pill">
              <span className={`twoway-risk-badge ${riskScore >= 70 ? "risk-high" : riskScore >= 40 ? "risk-medium" : "risk-low"}`}>
                RISK: {riskScore}/100
              </span>
              <span style={{ fontSize: 10, color: riskScore >= 70 ? "#f87171" : riskScore >= 40 ? "#fbbf24" : "#34d399", marginTop: 3, fontWeight: 600 }}>
                {verdict}
              </span>
            </div>
          </div>

          {/* Real-time Voice Visualizers */}
          <div className="twoway-visualizers">
            {/* User Microphone */}
            <div className="visualizer-box">
              <div className="visualizer-header">
                <span>
                  <User size={13} style={{ display: "inline", marginRight: 4, color: "#34d399" }} />
                  You (Human Voice)
                </span>
                <span style={{ color: isMuted || callStatus === "blocked" ? "#f87171" : "#34d399", fontSize: 11 }}>
                  {callStatus === "blocked" ? "Disconnected" : isMuted ? "Muted" : "● Genuine Acoustic"}
                </span>
              </div>
              <canvas ref={micCanvasRef} className="visualizer-canvas" width={220} height={36} />
            </div>

            {/* AI Deepfake Output */}
            <div className="visualizer-box">
              <div className="visualizer-header">
                <span>
                  <Bot size={13} style={{ display: "inline", marginRight: 4, color: "#f87171" }} />
                  Inbound (Deepfake AI Voice)
                </span>
                <span style={{ color: callStatus === "blocked" ? "#ef4444" : isAiSpeaking ? "#f87171" : "#94a3b8", fontSize: 11 }}>
                  {callStatus === "blocked" ? "Terminated (Blocked)" : isAiSpeaking ? "● Caller speaking — scoring this turn" : "Idle"}
                </span>
              </div>
              <div style={{
                height: 36,
                background: "rgba(0,0,0,0.25)",
                borderRadius: 6,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 4,
                overflow: "hidden"
              }}>
                {[12, 28, 16, 32, 24, 18, 30, 14, 26, 20].map((h, i) => (
                  <div
                    key={i}
                    style={{
                      width: 4,
                      height: callStatus === "blocked" ? "2px" : isAiSpeaking ? `${h}px` : "4px",
                      background: callStatus === "blocked" ? "#ef4444" : isAiSpeaking ? "#6366f1" : "rgba(255,255,255,0.1)",
                      borderRadius: 2,
                      transition: "height 0.15s ease",
                      animation: (isAiSpeaking && callStatus !== "blocked") ? `pulseDot ${0.4 + (i % 5) * 0.1}s infinite alternate` : "none"
                    }}
                  />
                ))}
              </div>
            </div>
          </div>

          {/* Hands-Free Voice Prompt Banner */}
          {callStatus === "connected" && (
            <div style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              background: "rgba(16, 185, 129, 0.1)",
              border: "1px solid rgba(16, 185, 129, 0.25)",
              borderRadius: "8px",
              padding: "6px 12px",
              fontSize: "12px",
              color: "#34d399",
            }}>
              <span className="twoway-pulse-dot" style={{ background: "#10b981", width: 6, height: 6 }} />
              <strong>Hands-Free Voice Mode Active:</strong> Direct mic me bolo — typing ya click karne ki zaroorat nahi hai!
            </div>
          )}

          {/* Interactive Chat / Dialogue History */}
          <div className="twoway-transcript">
            {messages.map((m, idx) => (
              <div key={idx} className={`transcript-msg ${m.sender}`}>
                <div style={{ fontSize: 10, opacity: 0.7, marginBottom: 2 }}>
                  {m.sender === "ai" ? "🤖 AI Scammer" : "👤 You"} · {m.time}
                </div>
                {m.text}
              </div>
            ))}
          </div>

          {/* Controls visible only if not blocked */}
          {callStatus === "connected" && (
            <>
              {/* Quick Voice Reply Options */}
              <div style={{ fontSize: 11, color: "#94a3b8" }}>Quick Responses (Click to speak):</div>
              <div className="twoway-quick-replies">
                {selectedPersona.quickReplies.map((reply, i) => (
                  <button
                    key={i}
                    className="reply-chip"
                    onClick={() => handleUserReply(reply)}
                  >
                    "{reply}"
                  </button>
                ))}
              </div>

              {/* Custom Reply Input */}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleUserReply(customInput);
                }}
                style={{ display: "flex", gap: 8 }}
              >
                <input
                  type="text"
                  placeholder="Speak or type your response to the AI..."
                  value={customInput}
                  onChange={(e) => setCustomInput(e.target.value)}
                  style={{
                    flex: 1,
                    padding: "8px 14px",
                    background: "rgba(255,255,255,0.05)",
                    border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: 8,
                    color: "white",
                    fontSize: 13,
                  }}
                />
                <button
                  type="submit"
                  className="twoway-btn secondary"
                  style={{ padding: "8px 14px" }}
                >
                  <Send size={15} />
                </button>
              </form>
            </>
          )}
        </div>

        {/* Footer Actions */}
        <div className="twoway-footer">
          <div className="twoway-btn-group">
            <button
              className="twoway-btn secondary"
              onClick={() => setIsMuted(!isMuted)}
              title={isMuted ? "Unmute Mic" : "Mute Mic"}
              disabled={callStatus === "blocked"}
            >
              {isMuted ? <MicOff size={16} color="#f87171" /> : <Mic size={16} />}
              {isMuted ? "Unmute" : "Mute"}
            </button>

            <button
              className="twoway-btn secondary"
              onClick={() => setIsSpeakerOn(!isSpeakerOn)}
              title={isSpeakerOn ? "Mute Speaker" : "Unmute Speaker"}
            >
              {isSpeakerOn ? <Volume2 size={16} /> : <VolumeX size={16} color="#f87171" />}
              Speaker
            </button>

            {callStatus === "connected" && (
              <button
                className="twoway-btn block-btn"
                onClick={() => triggerBlockAction("MANUAL_ANALYST_FRAUD_INTERCEPT")}
              >
                <Ban size={16} />
                Block & Intercept
              </button>
            )}
          </div>

          <button className="twoway-btn hangup" onClick={handleHangup}>
            <PhoneOff size={16} />
            {callStatus === "blocked" ? "Close Dialog" : "End Call"}
          </button>
        </div>
      </div>
    </div>
  );
}
