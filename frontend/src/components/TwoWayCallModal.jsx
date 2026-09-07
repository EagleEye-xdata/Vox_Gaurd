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

const SCAM_PERSONAS = [
  {
    id: "bank-otp",
    name: "Bank Security Fraud Phishing",
    callerType: "Deepfake AI Voice (Clone)",
    callerId: "+91 98210 44819 (Spoofed HDFC Desk)",
    initialPrompt: "Hello, main HDFC Fraud Prevention Department se Rohit bol raha hoon. Aapke account se abhi ₹49,999 ka international transaction trigger hua hai. Kya ye aapne kiya hai?",
    responses: [
      {
        trigger: "nahi",
        aiReply: "Theek hai sir, main turant transaction block kar raha hoon. Kripya transaction cancel karne ke liye aapke mobile par aaya hua 6-digit OTP confirm kijiye.",
        urgency: "HIGH",
      },
      {
        trigger: "kaun",
        aiReply: "Sir, main Senior Officer Rohit Verma hoon, Employee ID HDFC-9021. Hamara server hack attempt detect kar raha hai, turant OTP verify kijiye warna account freeze ho jayega.",
        urgency: "HIGH",
      },
      {
        trigger: "default",
        aiReply: "Samay bahut kam hai sir, fraud transfer hone se pehle kripya authorization code confirm kijiye.",
        urgency: "HIGH",
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
    name: "CFO Emergency Fund Transfer",
    callerType: "Cloned Executive Voice",
    callerId: "+91 98111 02931 (Executive Office)",
    initialPrompt: "Hi, this is Rajesh Sharma. I am in an urgent client meeting right now. We need to release the ₹2,50,000 vendor payment immediately. Has the wire been processed?",
    responses: [
      {
        trigger: "vendor",
        aiReply: "It is a new overseas supplier for the upcoming product launch. Process the wire transfer right now, I will sign the PO afterwards.",
        urgency: "HIGH",
      },
      {
        trigger: "default",
        aiReply: "Please approve this right away on priority. It is critical for the executive team.",
        urgency: "HIGH",
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
    name: "Family Emergency Extortion",
    callerType: "Deepfake AI Voice Clone",
    callerId: "+91 97182 33410 (Spoofed Private)",
    initialPrompt: "Papa, main bahut badi musibat mein hoon! Police ne accident case mein detain kar liya hai. Lawyer ko ₹50,000 turant transfer karne hain, please help karo!",
    responses: [
      {
        trigger: "kaha",
        aiReply: "Main Sector 18 police chowki ke paas hoon. Inspector phone nahi dene de rahe, turant paise bhejo papa!",
        urgency: "CRITICAL",
      },
      {
        trigger: "default",
        aiReply: "Papa please jaldi UPI karo, warna ye mujhe lockup me daal denge!",
        urgency: "CRITICAL",
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
  const [selectedPersona, setSelectedPersona] = useState(SCAM_PERSONAS[0]);
  const [callStatus, setCallStatus] = useState("connected"); // connected | blocked | ended
  const [isMuted, setIsMuted] = useState(false);
  const [isSpeakerOn, setIsSpeakerOn] = useState(true);
  const [messages, setMessages] = useState([]);
  const [customInput, setCustomInput] = useState("");
  const [activeCallId, setActiveCallId] = useState(null);
  const [riskScore, setRiskScore] = useState(25);
  const [verdict, setVerdict] = useState("ASSESSING");
  const [isAiSpeaking, setIsAiSpeaking] = useState(false);
  const [isListeningSpeech, setIsListeningSpeech] = useState(false);
  const [turnCount, setTurnCount] = useState(0);

  const micCanvasRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const streamRef = useRef(null);
  const animationFrameRef = useRef(null);
  const recognitionRef = useRef(null);

  // Initialize Audio & Gateway Live Stream Session
  useEffect(() => {
    if (!isOpen) return;

    setCallStatus("connected");
    setTurnCount(0);
    setMessages([
      { sender: "ai", text: selectedPersona.initialPrompt, time: new Date().toLocaleTimeString() }
    ]);
    setRiskScore(45);
    setVerdict("MONITORING (Evaluating Inbound Channel)");

    // Speak initial Deepfake AI message aloud
    speakAi(selectedPersona.initialPrompt);

    // Start Live Stream in VoxGuard Gateway
    let isCancelled = false;
    api("/stream/start", {
      filename: "fixture-steady.wav",
      label: `2-Way Call: Human vs ${selectedPersona.name}`,
      amount: 250000,
      known_beneficiary: false,
      new_beneficiary: true,
      request_urgency: "high",
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

  // Deepfake AI Speech Synthesis (simulates synthesized voice cadence)
  const speakAi = (text) => {
    if (!window.speechSynthesis || !isSpeakerOn || callStatus === "blocked") return;
    window.speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.04;
    utterance.pitch = 0.92;
    
    const voices = window.speechSynthesis.getVoices();
    const hindiOrIndianVoice = voices.find((v) =>
      v.lang.includes("hi-IN") || v.lang.includes("en-IN") || v.name.includes("India")
    );
    if (hindiOrIndianVoice) utterance.voice = hindiOrIndianVoice;

    utterance.onstart = () => {
      setIsAiSpeaking(true);
    };

    utterance.onend = () => {
      setIsAiSpeaking(false);
    };

    window.speechSynthesis.speak(utterance);
  };

  // Trigger Block & Intercept Action
  const triggerBlockAction = (reason = "CRITICAL_SYNTHETIC_RISK") => {
    setCallStatus("blocked");
    setRiskScore(96);
    setVerdict("REJECT_AND_BLOCK");
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
          risk_score: 96,
          action: "INBOUND_NUMBER_BLACKLISTED_AND_ACCOUNT_FROZEN",
          caller_id: selectedPersona.callerId,
          detector: "AASIST-L Neural Model",
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

    // If turns reach 2 or more, escalate risk and trigger auto-block
    if (newTurn >= 2) {
      setTimeout(() => {
        const escalationReply = "Kripya time mat waste kijiye, agar OTP nahi bataya toh abhi ke abhi aapka bank account permanent suspend ho jayega!";
        setMessages((prev) => [
          ...prev,
          { sender: "ai", text: escalationReply, time: new Date().toLocaleTimeString() }
        ]);
        speakAi(escalationReply);
        
        // Auto block on critical risk escalation after 2.2 seconds
        setTimeout(() => {
          triggerBlockAction("AUTOMATED_INTERVENTION_CRITICAL_SYNTHETIC_FRAUD");
        }, 2200);
      }, 500);
      return;
    }

    // Find AI response based on trigger keyword
    const lower = userText.toLowerCase();
    const matched = selectedPersona.responses.find((r) =>
      lower.includes(r.trigger)
    ) || selectedPersona.responses[selectedPersona.responses.length - 1];

    setTimeout(() => {
      const aiReplyText = matched.aiReply;
      setMessages((prev) => [
        ...prev,
        { sender: "ai", text: aiReplyText, time: new Date().toLocaleTimeString() }
      ]);
      speakAi(aiReplyText);
      setRiskScore(88);
      setVerdict("STEP_UP (Elevated Synthetic Risk)");
    }, 600);
  };

  const handleHangup = () => {
    setCallStatus("ended");
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

          {/* Persona Switcher Bar */}
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
              <Bot size={14} color="#818cf8" /> Deepfake Scenario:
            </span>
            <select
              value={selectedPersona.id}
              onChange={(e) => {
                const found = SCAM_PERSONAS.find((p) => p.id === e.target.value);
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
              {SCAM_PERSONAS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.callerType})
                </option>
              ))}
            </select>
          </div>

          {/* Caller Identity Card */}
          <div className="twoway-caller-card">
            <div className="twoway-caller-info">
              <div className="twoway-caller-avatar" style={{
                background: callStatus === "blocked" ? "linear-gradient(135deg, #dc2626, #7f1d1d)" : "linear-gradient(135deg, #4f46e5, #06b6d4)"
              }}>
                {callStatus === "blocked" ? <Ban size={22} /> : <Bot size={22} />}
              </div>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <strong style={{ fontSize: 14 }}>{selectedPersona.name}</strong>
                  <span style={{
                    fontSize: "10px",
                    background: "rgba(239, 68, 68, 0.18)",
                    color: "#f87171",
                    padding: "2px 6px",
                    borderRadius: "4px",
                    border: "1px solid rgba(239, 68, 68, 0.3)"
                  }}>
                    {selectedPersona.callerType}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>
                  {selectedPersona.callerId}
                </div>
              </div>
            </div>

            <div className="twoway-risk-pill">
              <span className={`twoway-risk-badge ${riskScore >= 70 ? "risk-high" : riskScore >= 40 ? "risk-medium" : "risk-low"}`}>
                RISK: {riskScore}/100
              </span>
              <span style={{ fontSize: 10, color: "#f87171", marginTop: 3, fontWeight: 600 }}>
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
                  {callStatus === "blocked" ? "Terminated (Blocked)" : isAiSpeaking ? "● Synthetic Artifacts Detected" : "Idle"}
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
