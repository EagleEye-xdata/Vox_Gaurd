import { useState, useRef, useEffect, useCallback } from "react";
import {
  Phone,
  PhoneOff,
  PhoneIncoming,
  PhoneForwarded,
  Mic,
  MicOff,
  Volume2,
  VolumeX,
  Wifi,
  WifiOff,
  Shield,
  AlertTriangle,
} from "lucide-react";
import "./webphone.css";

/**
 * WebPhone — Browser SIP softphone for VoxGuard live-call demos.
 *
 * Uses SIP.js SimpleUser to register with Asterisk over WSS and
 * place/receive calls. Calls to extension 7000 are monitored by
 * the ARI controller, which snoops the caller audio and feeds it
 * to the VoiceShield detector.
 *
 * Props:
 *   pbxHost  — hostname/IP of Asterisk (default: window.location.hostname)
 *   pbxPort  — WSS port (default: 8089)
 *   extension — SIP extension to register as (default: "1001")
 *   password  — SIP password
 *   monitoredExtension — extension that triggers ARI monitoring (default: "7000")
 */

const DEFAULT_PBX_HOST = window.location.hostname || "127.0.0.1";
const DEFAULT_PBX_PORT = "8089";
const DEFAULT_EXTENSION = "1001";
const DEFAULT_PASSWORD = "";
const MONITORED_EXT = "7000";

export default function WebPhone({
  pbxHost = DEFAULT_PBX_HOST,
  pbxPort = DEFAULT_PBX_PORT,
  extension = DEFAULT_EXTENSION,
  password = DEFAULT_PASSWORD,
  monitoredExtension = MONITORED_EXT,
}) {
  const [sipStatus, setSipStatus] = useState("disconnected"); // disconnected | connecting | registered
  const [callState, setCallState] = useState("idle"); // idle | calling | ringing | active | ended
  const [callTimer, setCallTimer] = useState(0);
  const [targetExt, setTargetExt] = useState(monitoredExtension);
  const [isMuted, setIsMuted] = useState(false);
  const [sipError, setSipError] = useState(null);
  const [config, setConfig] = useState({
    host: pbxHost,
    port: pbxPort,
    ext: extension,
    pass: password,
  });
  const [showConfig, setShowConfig] = useState(!password);

  const simpleUserRef = useRef(null);
  const timerRef = useRef(null);
  const audioRef = useRef(null);
  const callStartRef = useRef(null);

  // SIP.js is loaded dynamically to avoid build issues if not installed
  const sipjsRef = useRef(null);

  useEffect(() => {
    import("sip.js")
      .then((mod) => {
        sipjsRef.current = mod;
      })
      .catch(() => {
        setSipError("sip.js not installed. Run: npm install sip.js");
      });
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (simpleUserRef.current) {
        try {
          simpleUserRef.current.disconnect();
        } catch {}
      }
    };
  }, []);

  const startTimer = useCallback(() => {
    callStartRef.current = Date.now();
    setCallTimer(0);
    timerRef.current = setInterval(() => {
      setCallTimer(Math.floor((Date.now() - callStartRef.current) / 1000));
    }, 1000);
  }, []);

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60)
      .toString()
      .padStart(2, "0");
    const s = (seconds % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  };

  // -----------------------------------------------------------------------
  // SIP Registration
  // -----------------------------------------------------------------------

  const connect = useCallback(async () => {
    const SIP = sipjsRef.current;
    if (!SIP) {
      setSipError("sip.js not loaded yet");
      return;
    }

    setSipStatus("connecting");
    setSipError(null);

    const serverURI = `wss://${config.host}:${config.port}/ws`;

    try {
      const user = new SIP.Web.SimpleUser(serverURI, {
        aor: `sip:${config.ext}@${config.host}`,
        media: {
          constraints: { audio: true, video: false },
          remote: { audio: audioRef.current },
        },
        userAgentOptions: {
          authorizationUsername: config.ext,
          authorizationPassword: config.pass,
        },
        delegate: {
          onCallReceived: async () => {
            setCallState("ringing");
          },
          onCallAnswered: () => {
            setCallState("active");
            startTimer();
          },
          onCallHangup: () => {
            setCallState("ended");
            stopTimer();
            setTimeout(() => setCallState("idle"), 2000);
          },
          onRegistered: () => {
            setSipStatus("registered");
          },
          onUnregistered: () => {
            setSipStatus("disconnected");
          },
          onServerDisconnect: () => {
            setSipStatus("disconnected");
            setCallState("idle");
            stopTimer();
          },
        },
      });

      await user.connect();
      await user.register();
      simpleUserRef.current = user;
      setShowConfig(false);
    } catch (err) {
      setSipError(err.message || "Connection failed");
      setSipStatus("disconnected");
    }
  }, [config, startTimer, stopTimer]);

  const disconnect = useCallback(async () => {
    if (simpleUserRef.current) {
      try {
        await simpleUserRef.current.unregister();
        await simpleUserRef.current.disconnect();
      } catch {}
      simpleUserRef.current = null;
    }
    setSipStatus("disconnected");
    setCallState("idle");
    stopTimer();
  }, [stopTimer]);

  // -----------------------------------------------------------------------
  // Call Control
  // -----------------------------------------------------------------------

  const makeCall = useCallback(async () => {
    if (!simpleUserRef.current || !targetExt) return;
    setSipError(null);
    try {
      setCallState("calling");
      await simpleUserRef.current.call(`sip:${targetExt}@${config.host}`);
    } catch (err) {
      setSipError(err.message || "Call failed");
      setCallState("idle");
    }
  }, [targetExt, config.host]);

  const answerCall = useCallback(async () => {
    if (!simpleUserRef.current) return;
    try {
      await simpleUserRef.current.answer();
    } catch (err) {
      setSipError(err.message);
    }
  }, []);

  const hangup = useCallback(async () => {
    if (!simpleUserRef.current) return;
    try {
      await simpleUserRef.current.hangup();
    } catch {}
    setCallState("ended");
    stopTimer();
    setTimeout(() => setCallState("idle"), 2000);
  }, [stopTimer]);

  const toggleMute = useCallback(async () => {
    if (!simpleUserRef.current) return;
    try {
      if (isMuted) {
        await simpleUserRef.current.unmute();
      } else {
        await simpleUserRef.current.mute();
      }
      setIsMuted(!isMuted);
    } catch {}
  }, [isMuted]);

  // -----------------------------------------------------------------------
  // Dial pad digits
  // -----------------------------------------------------------------------
  const dialPadDigits = [
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "*",
    "0",
    "#",
  ];

  const pressDigit = (digit) => {
    if (callState === "active" && simpleUserRef.current) {
      try {
        simpleUserRef.current.sendDTMF(digit);
      } catch {}
    } else {
      setTargetExt((prev) => prev + digit);
    }
  };

  // -----------------------------------------------------------------------
  // Status indicators
  // -----------------------------------------------------------------------
  const isMonitoredCall =
    targetExt === monitoredExtension || targetExt.startsWith("700");

  const statusColor =
    sipStatus === "registered"
      ? "var(--wp-green)"
      : sipStatus === "connecting"
        ? "var(--wp-amber)"
        : "var(--wp-red)";

  const statusLabel =
    sipStatus === "registered"
      ? `Registered as ${config.ext}`
      : sipStatus === "connecting"
        ? "Connecting…"
        : "Disconnected";

  return (
    <div className="webphone">
      {/* Hidden audio element for remote media */}
      <audio ref={audioRef} autoPlay style={{ display: "none" }} />

      {/* Header */}
      <div className="wp-header">
        <div className="wp-header-left">
          <Phone size={18} />
          <span className="wp-title">VoxGuard Softphone</span>
        </div>
        <div className="wp-status" style={{ color: statusColor }}>
          {sipStatus === "registered" ? (
            <Wifi size={14} />
          ) : (
            <WifiOff size={14} />
          )}
          <span>{statusLabel}</span>
        </div>
      </div>

      {/* Config panel (shown when not connected) */}
      {showConfig && (
        <div className="wp-config">
          <div className="wp-config-row">
            <label>PBX Host</label>
            <input
              value={config.host}
              onChange={(e) =>
                setConfig((c) => ({ ...c, host: e.target.value }))
              }
              placeholder="pbx.local"
            />
          </div>
          <div className="wp-config-row">
            <label>WSS Port</label>
            <input
              value={config.port}
              onChange={(e) =>
                setConfig((c) => ({ ...c, port: e.target.value }))
              }
              placeholder="8089"
            />
          </div>
          <div className="wp-config-row">
            <label>Extension</label>
            <input
              value={config.ext}
              onChange={(e) =>
                setConfig((c) => ({ ...c, ext: e.target.value }))
              }
              placeholder="1001"
            />
          </div>
          <div className="wp-config-row">
            <label>Password</label>
            <input
              type="password"
              value={config.pass}
              onChange={(e) =>
                setConfig((c) => ({ ...c, pass: e.target.value }))
              }
              placeholder="SIP secret"
            />
          </div>
        </div>
      )}

      {/* Error display */}
      {sipError && (
        <div className="wp-error">
          <AlertTriangle size={14} />
          <span>{sipError}</span>
        </div>
      )}

      {/* Call display */}
      {callState !== "idle" && (
        <div className={`wp-call-display wp-call-${callState}`}>
          {callState === "calling" && (
            <>
              <PhoneForwarded size={20} className="wp-pulse" />
              <span>Calling {targetExt}…</span>
            </>
          )}
          {callState === "ringing" && (
            <>
              <PhoneIncoming size={20} className="wp-ring" />
              <span>Incoming call</span>
            </>
          )}
          {callState === "active" && (
            <>
              <Phone size={20} style={{ color: "var(--wp-green)" }} />
              <span className="wp-timer">{formatTime(callTimer)}</span>
              {isMonitoredCall && (
                <span className="wp-monitored-badge">
                  <Shield size={12} /> MONITORED
                </span>
              )}
            </>
          )}
          {callState === "ended" && (
            <>
              <PhoneOff size={20} />
              <span>Call ended — {formatTime(callTimer)}</span>
            </>
          )}
        </div>
      )}

      {/* Dial target */}
      {callState === "idle" && sipStatus === "registered" && (
        <div className="wp-dial-target">
          <input
            value={targetExt}
            onChange={(e) =>
              setTargetExt(e.target.value.replace(/[^0-9*#]/g, ""))
            }
            placeholder="Extension"
            className="wp-dial-input"
          />
          {isMonitoredCall && (
            <span className="wp-monitored-hint">
              <Shield size={12} /> AI-Monitored
            </span>
          )}
        </div>
      )}

      {/* Dial pad */}
      {callState === "idle" && sipStatus === "registered" && (
        <div className="wp-dialpad">
          {dialPadDigits.map((d) => (
            <button key={d} className="wp-digit" onClick={() => pressDigit(d)}>
              {d}
            </button>
          ))}
        </div>
      )}

      {/* Action buttons */}
      <div className="wp-actions">
        {sipStatus !== "registered" && (
          <button
            className="wp-btn wp-btn-connect"
            onClick={connect}
            disabled={sipStatus === "connecting"}
          >
            <Wifi size={16} />
            {sipStatus === "connecting" ? "Connecting…" : "Connect"}
          </button>
        )}

        {sipStatus === "registered" && callState === "idle" && (
          <>
            <button
              className="wp-btn wp-btn-call"
              onClick={makeCall}
              disabled={!targetExt}
            >
              <Phone size={16} />
              Call
            </button>
            <button
              className="wp-btn wp-btn-config"
              onClick={() => setShowConfig(!showConfig)}
            >
              ⚙
            </button>
            <button className="wp-btn wp-btn-disconnect" onClick={disconnect}>
              <WifiOff size={16} />
            </button>
          </>
        )}

        {callState === "ringing" && (
          <>
            <button className="wp-btn wp-btn-answer" onClick={answerCall}>
              <Phone size={16} />
              Answer
            </button>
            <button className="wp-btn wp-btn-hangup" onClick={hangup}>
              <PhoneOff size={16} />
              Reject
            </button>
          </>
        )}

        {(callState === "active" || callState === "calling") && (
          <>
            <button
              className={`wp-btn ${isMuted ? "wp-btn-unmute" : "wp-btn-mute"}`}
              onClick={toggleMute}
            >
              {isMuted ? <MicOff size={16} /> : <Mic size={16} />}
              {isMuted ? "Unmute" : "Mute"}
            </button>
            <button className="wp-btn wp-btn-hangup" onClick={hangup}>
              <PhoneOff size={16} />
              Hang Up
            </button>
          </>
        )}
      </div>
    </div>
  );
}
