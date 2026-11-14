import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bell,
  Camera,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  Ellipsis,
  FileClock,
  Fingerprint,
  Gauge,
  KeyRound,
  LockKeyhole,
  Menu,
  MonitorCheck,
  MoreHorizontal,
  Play,
  ScanFace,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Video,
  X,
  Zap,
} from "lucide-react";
import type { ReactNode, RefObject } from "react";

type PageId = "terminal" | "audit" | "analytics" | "policies";
type Outcome = "ALLOW" | "VERIFY" | "DENY";
type Environment = "Staging" | "Production";

type Session = {
  id: string;
  timestamp: string;
  tier: string;
  confidence: number;
  terminalId: string;
  status: Outcome;
  latency: number;
  reason: string;
};

type Scenario = {
  name: string;
  outcome: Outcome;
  confidence: number;
  reason: string;
  tier: string;
  latency: number;
};

const pageItems: { id: PageId; label: string; icon: typeof ScanFace }[] = [
  { id: "terminal", label: "Live Verification Node", icon: ScanFace },
  { id: "audit", label: "Audit & session logs", icon: FileClock },
  { id: "analytics", label: "Analytics & latency", icon: Gauge },
  { id: "policies", label: "Security policies", icon: SlidersHorizontal },
];

const scenarios: Scenario[] = [
  {
    name: "Genuine customer",
    outcome: "ALLOW",
    confidence: 98.4,
    reason: "Liveness and identity checks passed.",
    tier: "Tier 1 · Passive",
    latency: 0.94,
  },
  {
    name: "Printed photo",
    outcome: "DENY",
    confidence: 99.1,
    reason: "Presentation attack indicators detected.",
    tier: "Tier 1 · Passive",
    latency: 0.82,
  },
  {
    name: "Low-light capture",
    outcome: "VERIFY",
    confidence: 72.6,
    reason: "Image quality is below the configured confidence threshold.",
    tier: "Tier 2 · Step-up",
    latency: 1.08,
  },
  {
    name: "Screen replay",
    outcome: "DENY",
    confidence: 97.8,
    reason: "Display replay pattern detected.",
    tier: "Tier 1 · Passive",
    latency: 0.89,
  },
  {
    name: "Identity mismatch",
    outcome: "DENY",
    confidence: 96.2,
    reason: "Captured face does not match the enrolled identity.",
    tier: "Tier 1 · Passive",
    latency: 1.02,
  },
  {
    name: "Video replay",
    outcome: "VERIFY",
    confidence: 68.3,
    reason: "Temporal inconsistency requires an active challenge.",
    tier: "Tier 3 · Active",
    latency: 1.16,
  },
];

const initialSessions: Session[] = [
  {
    id: "VA-8F2A91",
    timestamp: "Today, 10:42:18",
    tier: "Tier 1 · Passive",
    confidence: 99.2,
    terminalId: "LOBBY-ID-04",
    status: "ALLOW",
    latency: 0.82,
    reason: "Liveness and identity checks passed.",
  },
  {
    id: "VA-8F2A90",
    timestamp: "Today, 10:41:53",
    tier: "Tier 2 · Step-up",
    confidence: 84.1,
    terminalId: "LOBBY-ID-02",
    status: "VERIFY",
    latency: 1.16,
    reason: "Active challenge required.",
  },
  {
    id: "VA-8F2A8F",
    timestamp: "Today, 10:40:27",
    tier: "Tier 1 · Passive",
    confidence: 98.8,
    terminalId: "BRANCH-014",
    status: "DENY",
    latency: 0.91,
    reason: "Presentation attack detected.",
  },
  {
    id: "VA-8F2A8E",
    timestamp: "Today, 10:38:05",
    tier: "Tier 1 · Passive",
    confidence: 97.6,
    terminalId: "LOBBY-ID-01",
    status: "ALLOW",
    latency: 1.02,
    reason: "Liveness and identity checks passed.",
  },
  {
    id: "VA-8F2A8D",
    timestamp: "Today, 10:36:49",
    tier: "Tier 3 · Active",
    confidence: 71.4,
    terminalId: "BRANCH-006",
    status: "VERIFY",
    latency: 1.14,
    reason: "Video replay indicators require step-up.",
  },
  {
    id: "VA-8F2A8C",
    timestamp: "Today, 10:34:12",
    tier: "Tier 1 · Passive",
    confidence: 99.4,
    terminalId: "LOBBY-ID-04",
    status: "ALLOW",
    latency: 0.76,
    reason: "Liveness and identity checks passed.",
  },
];

function makeSession(scenario: Scenario): Session {
  const now = new Date();
  return {
    id: `VA-${crypto.randomUUID().slice(0, 6).toUpperCase()}`,
    timestamp: `Today, ${now.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })}`,
    tier: scenario.tier,
    confidence: scenario.confidence,
    terminalId: "ID-01",
    status: scenario.outcome,
    latency: scenario.latency,
    reason: scenario.reason,
  };
}

function StatusBadge({ status }: { status: Outcome }) {
  const styles: Record<Outcome, string> = {
    ALLOW: "status-allow",
    VERIFY: "status-verify",
    DENY: "status-deny",
  };
  const text: Record<Outcome, string> = {
    ALLOW: "Allowed",
    VERIFY: "Review required",
    DENY: "Denied",
  };
  return <span className={`status-badge ${styles[status]}`}>{text[status]}</span>;
}

function SectionHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-header">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}

function MetricCard({
  label,
  value,
  change,
  icon: Icon,
  iconClass = "icon-blue",
  footnote,
}: {
  label: string;
  value: string;
  change?: string;
  icon: typeof Activity;
  iconClass?: string;
  footnote: string;
}) {
  return (
    <article className="metric-card">
      <div className="metric-top">
        <span>{label}</span>
        <span className={`metric-icon ${iconClass}`}>
          <Icon size={17} />
        </span>
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-foot">
        {change && <span className="metric-change">{change}</span>}
        <span>{footnote}</span>
      </div>
    </article>
  );
}

function App() {
  const [page, setPage] = useState<PageId>("terminal");
  const [environment, setEnvironment] = useState<Environment>("Staging");
  const [sessions, setSessions] = useState<Session[]>(initialSessions);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | Outcome>("ALL");
  const [selectedScenario, setSelectedScenario] = useState(0);
  const [scanState, setScanState] = useState<"idle" | "scanning" | "result">("idle");
  const [decision, setDecision] = useState<Scenario | null>(null);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [challengeComplete, setChallengeComplete] = useState(false);
  const [otp, setOtp] = useState("");
  const [otpAttempts, setOtpAttempts] = useState(0);
  const [policySaved, setPolicySaved] = useState(false);
  const [evidenceNotice, setEvidenceNotice] = useState("");
  const [cameraError, setCameraError] = useState("");
  const [cameraEnabled, setCameraEnabled] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [confidenceThreshold, setConfidenceThreshold] = useState(85);
  const [otpRequired, setOtpRequired] = useState(true);
  const [passiveBurst, setPassiveBurst] = useState(true);
  const [video, setVideo] = useState<HTMLVideoElement | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const pageMeta = pageItems.find((item) => item.id === page)!;

  useEffect(() => {
    if (video && streamRef.current) video.srcObject = streamRef.current;
  }, [video, cameraEnabled]);

  useEffect(
    () => () => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
    },
    [],
  );

  const filteredSessions = useMemo(() => {
    const query = search.trim().toLowerCase();
    return sessions.filter((session) => {
      const matchesSearch =
        !query ||
        [
          session.id,
          session.terminalId,
          session.tier,
          session.status,
          session.reason,
        ].some((field) => field.toLowerCase().includes(query));
      return matchesSearch && (statusFilter === "ALL" || session.status === statusFilter);
    });
  }, [search, sessions, statusFilter]);

  function runDemoDecision() {
    if (scanState === "scanning") return;
    setScanState("scanning");
    setDecision(null);
    setActiveSessionId("");
    setChallengeComplete(false);
    window.setTimeout(() => {
      const scenario = scenarios[selectedScenario];
      setDecision(scenario);
      const session = makeSession(scenario);
      setActiveSessionId(session.id);
      setSessions((current) => [session, ...current]);
      setScanState("result");
    }, 1100);
  }

  function nextUser() {
    setDecision(null);
    setScanState("idle");
    setOtp("");
    setOtpAttempts(0);
    setChallengeComplete(false);
    setCameraError("");
  }

  async function enableCamera() {
    setCameraError("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraError("Camera access requires HTTPS or localhost and a supported browser.");
      return;
    }
    try {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      setCameraEnabled(true);
      if (videoRef.current) videoRef.current.srcObject = streamRef.current;
      setVideo(videoRef.current);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Camera permission was not granted.";
      setCameraError(`Camera unavailable: ${message}`);
      setCameraEnabled(false);
    }
  }

  function exportCsv() {
    const headers = [
      "Session ID",
      "Timestamp",
      "Tier Triggered",
      "Confidence Score",
      "Terminal ID",
      "Status",
      "Latency (s)",
      "Reason",
    ];
    const quoteCsv = (value: string | number) =>
      `"${String(value).replaceAll('"', '""').replace(/^[=+\-@]/, "'$&")}"`;
    const rows = filteredSessions.map((session) =>
      [
        session.id,
        session.timestamp,
        session.tier,
        `${session.confidence.toFixed(1)}%`,
        session.terminalId,
        session.status,
        session.latency.toFixed(2),
        session.reason,
      ]
        .map(quoteCsv)
        .join(","),
    );
    const csv = [headers.map(quoteCsv).join(","), ...rows].join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `verifyaura-audit-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    setEvidenceNotice("CSV exported from local demo records.");
  }

  function requestEvidence(session: Session) {
    setEvidenceNotice(
      `Evidence request prepared for ${session.id}. S3/KMS storage is not connected in this demo.`,
    );
  }

  function submitOtp() {
    if (otp.length !== 6 || !/^\d{6}$/.test(otp)) return;
    if (otpAttempts >= 3) {
      setEvidenceNotice("Demo step-up is locked after three unsuccessful attempts.");
      return;
    }
    if (otp === "123456") {
      setChallengeComplete(true);
      setEvidenceNotice("Demo OTP accepted. This does not verify a real identity or grant access.");
      return;
    }
    const attemptCount = otpAttempts + 1;
    setOtpAttempts(attemptCount);
    setEvidenceNotice(
      attemptCount >= 3
        ? "Demo step-up locked after three unsuccessful attempts."
        : `Demo OTP not accepted. ${3 - attemptCount} attempts remain.`,
    );
  }

  const nav = (
    <>
      <div className="sidebar-label">WORKSPACE</div>
      <nav className="side-nav" aria-label="Main navigation">
        {pageItems.map(({ id, label, icon: Icon }, index) => (
          <button
            className={`nav-item ${page === id ? "active" : ""}`}
            key={id}
            onClick={() => {
              setPage(id);
              setEvidenceNotice("");
              setSidebarOpen(false);
            }}
            aria-current={page === id ? "page" : undefined}
          >
            <Icon size={17} strokeWidth={1.8} />
            <span>{index + 1}. {label}</span>
            {id === "audit" && <span className="nav-count">{sessions.length}</span>}
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="help-card">
          <CircleHelp size={17} />
          <div>
            <strong>Need assistance?</strong>
            <span>View operator guide</span>
          </div>
          <ArrowRight size={14} />
        </div>
        <div className="workspace-card">
          <div className="workspace-avatar">VA</div>
          <div className="workspace-copy">
            <strong>Acme Financial</strong>
            <span>Enterprise workspace</span>
          </div>
          <MoreHorizontal size={17} />
        </div>
      </div>
    </>
  );

  return (
    <div className="app-shell min-h-screen bg-slate-50 text-slate-900">
      <header className="topbar">
        <button
          className="mobile-menu icon-button"
          aria-label="Toggle navigation"
          onClick={() => setSidebarOpen((open) => !open)}
        >
          <Menu size={19} />
        </button>
        <a className="brand" href="#" onClick={(event) => event.preventDefault()}>
          <span className="brand-symbol"><ShieldCheck size={19} /></span>
          <span>Verify<span className="brand-accent">Aura</span></span>
          <span className="brand-divider" />
          <span className="brand-product">Identity assurance</span>
        </a>
        <div className="topbar-right">
          <label className="environment-picker">
            <span className={`environment-dot ${environment.toLowerCase()}`} />
            <select
              aria-label="Environment"
              value={environment}
              onChange={(event) => setEnvironment(event.target.value as Environment)}
            >
              <option>Staging</option>
              <option>Production</option>
            </select>
            <ChevronDown size={14} />
          </label>
          <div className="edge-connection" title="Liveness Pipeline Ready — simulation only; no biometric service is connected">
            <span className="connection-dot" />
            <span>Liveness Pipeline Ready · Demo</span>
          </div>
          <button className="icon-button notification-button" aria-label="Notifications">
            <Bell size={18} />
            <span className="notification-dot" />
          </button>
          <button className="profile-button" aria-label="User profile">
            <span className="profile-avatar">JD</span>
            <span className="profile-name">Jordan Davis</span>
            <ChevronDown size={14} />
          </button>
        </div>
      </header>

      <div className="layout">
        {sidebarOpen && <button className="mobile-backdrop" onClick={() => setSidebarOpen(false)} aria-label="Close navigation" />}
        <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
          <div className="workspace-heading">
            <div className="workspace-mark"><Shield size={16} /></div>
            <div><strong>Security operations</strong><span>Control center</span></div>
          </div>
          {nav}
        </aside>

        <main className="main-content">
          <div className="breadcrumb">
            <span>VerifyAura</span><ChevronRight size={14} /><span>{pageMeta.label}</span>
          </div>

          {page === "terminal" && (
            <TerminalPage
              decision={decision}
              activeSessionId={activeSessionId}
              scanState={scanState}
              selectedScenario={selectedScenario}
              setSelectedScenario={setSelectedScenario}
              runDemoDecision={runDemoDecision}
              nextUser={nextUser}
              cameraEnabled={cameraEnabled}
              cameraError={cameraError}
              videoRef={videoRef}
              enableCamera={enableCamera}
              passiveBurst={passiveBurst}
              challengeComplete={challengeComplete}
              setChallengeComplete={setChallengeComplete}
              otp={otp}
              setOtp={setOtp}
              otpAttempts={otpAttempts}
              submitOtp={submitOtp}
              evidenceNotice={evidenceNotice}
            />
          )}
          {page === "audit" && (
            <AuditPage
              sessions={filteredSessions}
              search={search}
              setSearch={setSearch}
              statusFilter={statusFilter}
              setStatusFilter={setStatusFilter}
              exportCsv={exportCsv}
              requestEvidence={requestEvidence}
              evidenceNotice={evidenceNotice}
              clearNotice={() => setEvidenceNotice("")}
            />
          )}
          {page === "analytics" && <AnalyticsPage sessions={sessions} />}
          {page === "policies" && (
            <PoliciesPage
              confidenceThreshold={confidenceThreshold}
              setConfidenceThreshold={setConfidenceThreshold}
              passiveBurst={passiveBurst}
              setPassiveBurst={setPassiveBurst}
              otpRequired={otpRequired}
              setOtpRequired={setOtpRequired}
              policySaved={policySaved}
              savePolicies={() => {
                setPolicySaved(true);
                window.setTimeout(() => setPolicySaved(false), 2600);
              }}
            />
          )}

          <footer className="app-footer">
            <div><span className="footer-dot" /> Demo UI · no biometric backend connected</div>
            <div><span>Latency values are simulated</span><span className="footer-separator">•</span><span>Audit records are sample data</span></div>
            <div>VerifyAura Console <span className="footer-separator">·</span> v1.0.0</div>
          </footer>
        </main>
      </div>
    </div>
  );
}

function TerminalPage({
  decision,
  activeSessionId,
  scanState,
  selectedScenario,
  setSelectedScenario,
  runDemoDecision,
  nextUser,
  cameraEnabled,
  cameraError,
  videoRef,
  enableCamera,
  passiveBurst,
  challengeComplete,
  setChallengeComplete,
  otp,
  setOtp,
  otpAttempts,
  submitOtp,
  evidenceNotice,
}: {
  decision: Scenario | null;
  activeSessionId: string;
  scanState: "idle" | "scanning" | "result";
  selectedScenario: number;
  setSelectedScenario: (index: number) => void;
  runDemoDecision: () => void;
  nextUser: () => void;
  cameraEnabled: boolean;
  cameraError: string;
  videoRef: RefObject<HTMLVideoElement>;
  enableCamera: () => void;
  passiveBurst: boolean;
  challengeComplete: boolean;
  setChallengeComplete: (complete: boolean) => void;
  otp: string;
  setOtp: (value: string) => void;
  otpAttempts: number;
  submitOtp: () => void;
  evidenceNotice: string;
}) {
  const isScanning = scanState === "scanning";
  const isAllow = decision?.outcome === "ALLOW";
  const isDeny = decision?.outcome === "DENY";
  const isVerify = decision?.outcome === "VERIFY";

  return (
    <>
      <SectionHeader
        eyebrow="IDENTITY ASSURANCE / TERMINAL"
        title="Live Verification Node"
        description="Run a guided verification demonstration. This workspace uses simulated decisions only."
        action={<span className="demo-pill"><span /> SIMULATION WORKSPACE</span>}
      />
      <div className="notice notice-info">
        <AlertTriangle size={17} />
        <span><strong>Demonstration environment.</strong> Camera preview is local to this browser. No video is uploaded and no biometric decision service is connected.</span>
        <button aria-label="Dismiss demo notice"><X size={15} /></button>
      </div>

      <div className="terminal-grid">
        <section className="card capture-card">
          <div className="card-heading">
            <div>
              <div className="card-title">Secure Verification Terminal</div>
              <div className="card-subtitle">Terminal ID <strong>ID-01</strong></div>
            </div>
            <span className="device-pill"><span /> Liveness Pipeline Ready · Demo</span>
          </div>
          <div className={`camera-frame ${isScanning ? "is-scanning" : ""}`}>
            {cameraEnabled ? (
              <video ref={videoRef} className="camera-video" autoPlay muted playsInline />
            ) : (
              <div className="camera-placeholder">
                <div className="camera-placeholder-icon"><Video size={28} /></div>
                <strong>Camera preview</strong>
                <span>Enable camera access for a local preview</span>
                <button className="button button-secondary button-small" onClick={enableCamera}>
                  <Camera size={15} /> Enable camera
                </button>
              </div>
            )}
            <div className="face-reticle" aria-hidden="true">
              <i className="reticle-corner corner-tl" /><i className="reticle-corner corner-tr" />
              <i className="reticle-corner corner-bl" /><i className="reticle-corner corner-br" />
              <span className="reticle-guide" />
            </div>
            {isScanning && (
              <div className="scan-overlay">
                <span className="spinner" />
                <span>Evaluating demonstration session…</span>
              </div>
            )}
            {isAllow && <div className="camera-state-chip success-chip"><CheckCircle2 size={14} /> Verified</div>}
            {isDeny && <div className="camera-state-chip deny-chip"><ShieldAlert size={14} /> Blocked</div>}
          </div>
          {cameraError && <div className="inline-error"><AlertTriangle size={15} />{cameraError}</div>}
          <div className="camera-caption"><span><span className="privacy-dot" /> Local preview only</span><span>720p · Face framing guide</span></div>
          <div className="capture-controls">
            <label className="scenario-select">
              <span>Demo scenario</span>
              <select
                value={selectedScenario}
                onChange={(event) => setSelectedScenario(Number(event.target.value))}
                disabled={isScanning}
              >
                {scenarios.map((scenario, index) => (
                  <option value={index} key={scenario.name}>{scenario.name}</option>
                ))}
              </select>
            </label>
            <button
              className="button button-primary run-button"
              onClick={runDemoDecision}
              disabled={isScanning}
            >
              {isScanning ? <><span className="spinner spinner-white" /> Processing secure scan…</> : <><Play size={16} fill="currentColor" /> Initiate Secure Scan</>}
            </button>
          </div>
          <div className="capture-note"><LockKeyhole size={13} /> Demo uses synthetic outcomes and illustrative confidence values.</div>
        </section>

        <section className="card decision-card">
          <div className="card-heading">
            <div>
              <div className="card-title">Verification result</div>
              <div className="card-subtitle">
                Biometric Session {activeSessionId ? "Active" : "Ready"}
                {activeSessionId && <> · <strong>{activeSessionId}</strong></>}
                {!activeSessionId && <strong> · No decision initiated</strong>}
              </div>
            </div>
            {decision && <span className="session-time"><Clock3 size={13} /> just now</span>}
          </div>

          {isScanning && (
            <div className="scanning-panel" role="status" aria-live="polite">
              <span className="spinner" />
              <strong>Tiered Decision Engine · Simulation</strong>
              <span>Evaluating a sample liveness and identity decision</span>
              <div className="progress-track"><div className="progress-fill" /></div>
              <div className="scan-steps">
                <span className="step-done"><Check size={13} /> Frame prepared</span>
                <span><Activity size={13} /> Decision analysis · simulated</span>
              </div>
            </div>
          )}

          {!decision && !isScanning && (
            <div className="empty-decision">
              <div className="empty-icon"><Fingerprint size={28} /></div>
              <strong>Biometric Session Ready · Demo</strong>
              <span>Select an illustrative scenario and initiate a secure scan to preview an outcome.</span>
              <div className="empty-capabilities">
                <span><CheckCircle2 size={14} /> Passive burst {passiveBurst ? "enabled" : "disabled"}</span>
                <span><CheckCircle2 size={14} /> Liveness workflow preview</span>
              </div>
            </div>
          )}

          {decision && !isScanning && (
            <div className="result-content" aria-live="polite">
              <div className={`decision-banner ${isAllow ? "banner-allow" : isDeny ? "banner-deny" : "banner-verify"}`}>
                <div className="decision-symbol">
                  {isAllow ? <CheckCircle2 size={22} /> : isDeny ? <X size={22} /> : <AlertTriangle size={22} />}
                </div>
                <div>
                  <div className="decision-label">{isAllow ? "ALLOW" : isDeny ? "DENY" : "STEP-UP REQUIRED"}</div>
                  <strong>{isAllow ? "Identity verified" : isDeny ? "Verification blocked" : "Additional verification"}</strong>
                  <span>{decision.reason}</span>
                </div>
              </div>

              <div className="result-metrics">
                <div className="result-metric">
                  <span>Illustrative confidence</span>
                  <strong>{decision.confidence.toFixed(1)}%</strong>
                  <div className="confidence-bar"><span style={{ width: `${decision.confidence}%` }} /></div>
                </div>
                <div className="result-metric">
                  <span>Simulated latency</span>
                  <strong>{decision.latency.toFixed(2)}<small> s</small></strong>
                  <span className="latency-target"><Zap size={12} /> Target &lt; 1.20 s</span>
                </div>
              </div>

              <div className="tier-row">
                <span>Decision path</span><strong>{decision.tier}</strong>
              </div>

              {isVerify && (
                <div className="step-up-panel">
                  <div className="step-up-heading">
                    <span className="step-up-icon"><ScanFace size={17} /></span>
                    <div><strong>Multi-Factor Identity Resolution</strong><span>Tier 2 challenge · simulation only, no biometric validation</span></div>
                  </div>
                  <div className="challenge-instruction">
                    <Sparkles size={16} />
                    <span>Turn your head slightly to the left, then confirm the action.</span>
                  </div>
                  <button
                    className={`button ${challengeComplete ? "button-success" : "button-secondary"} button-full`}
                    onClick={() => setChallengeComplete(!challengeComplete)}
                  >
                    {challengeComplete ? <><Check size={15} /> Action marked complete (demo)</> : "Confirm challenge action"}
                  </button>
                  <div className="otp-row">
                    <label htmlFor="demo-otp">Demo OTP</label>
                    <input
                      id="demo-otp"
                      inputMode="numeric"
                      maxLength={6}
                      placeholder="6-digit code"
                      value={otp}
                      onChange={(event) => setOtp(event.target.value.replace(/\D/g, "").slice(0, 6))}
                      disabled={otpAttempts >= 3}
                    />
                    <button className="button button-primary button-small" onClick={submitOtp} disabled={otp.length !== 6 || otpAttempts >= 3}>
                      Verify OTP
                    </button>
                  </div>
                  <span className="attempt-count">Demo code: 123456 · not a real credential</span>
                  <span className="attempt-count">{otpAttempts}/3 demo attempts used</span>
                </div>
              )}

              {evidenceNotice && <div className="inline-notice"><AlertTriangle size={14} />{evidenceNotice}</div>}
              <button
                className={`button ${isAllow ? "button-primary" : "button-secondary"} button-full next-user-button`}
                onClick={nextUser}
              >
                {isAllow ? "Next identity session" : "Reset verification session"} <ArrowRight size={15} />
              </button>
            </div>
          )}

          <div className="decision-footer"><span><ShieldCheck size={14} /> Demo policy set</span><span>Tier 1{passiveBurst ? " · passive burst" : ""}</span></div>
        </section>
      </div>

      <div className="terminal-bottom-grid">
        <div className="card mini-info-card">
          <div className="mini-icon blue-mini"><MonitorCheck size={17} /></div>
          <div><strong>Identity node readiness</strong><span>Local browser preview · backend not connected</span></div>
          <span className="status-badge status-verify">Demo</span>
        </div>
        <div className="card mini-info-card">
          <div className="mini-icon green-mini"><LockKeyhole size={17} /></div>
          <div><strong>Evidence handling</strong><span>No image upload or S3/KMS storage in this demo</span></div>
          <span className="quiet-label">Local only</span>
        </div>
      </div>
    </>
  );
}

function AuditPage({
  sessions,
  search,
  setSearch,
  statusFilter,
  setStatusFilter,
  exportCsv,
  requestEvidence,
  evidenceNotice,
  clearNotice,
}: {
  sessions: Session[];
  search: string;
  setSearch: (value: string) => void;
  statusFilter: "ALL" | Outcome;
  setStatusFilter: (value: "ALL" | Outcome) => void;
  exportCsv: () => void;
  requestEvidence: (session: Session) => void;
  evidenceNotice: string;
  clearNotice: () => void;
}) {
  return (
    <>
      <SectionHeader
        eyebrow="GOVERNANCE / AUDIT"
        title="Audit & session logs"
        description="Searchable sample verification activity for compliance review. Records shown are synthetic demo data."
        action={<button className="button button-secondary" onClick={exportCsv}><Download size={15} /> Export CSV</button>}
      />
      <div className="notice notice-info compact-notice">
        <AlertTriangle size={16} /><span>Sample audit records only. Evidence actions are simulated; no S3 or KMS service is connected.</span>
      </div>
      {evidenceNotice && (
        <div className="notice notice-success">
          <CheckCircle2 size={16} /><span>{evidenceNotice}</span>
          <button onClick={clearNotice} aria-label="Dismiss notice"><X size={15} /></button>
        </div>
      )}
      <section className="card audit-card">
        <div className="table-toolbar">
          <div>
            <h2>Verification events</h2>
            <p>{sessions.length} matching records · timestamps in local time</p>
          </div>
          <div className="table-filters">
            <label className="search-field">
              <Search size={16} />
              <input aria-label="Search audit sessions" placeholder="Search sessions…" value={search} onChange={(event) => setSearch(event.target.value)} />
            </label>
            <select aria-label="Filter by status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as "ALL" | Outcome)}>
              <option value="ALL">All statuses</option>
              <option value="ALLOW">Allowed</option>
              <option value="VERIFY">Review required</option>
              <option value="DENY">Denied</option>
            </select>
            <button className="icon-button filter-button" aria-label="Additional filters"><SlidersHorizontal size={16} /></button>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Session ID</th><th>Timestamp</th><th>Tier triggered</th>
                <th>Confidence</th><th>Terminal ID</th><th>Status</th><th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.id}>
                  <td><button className="session-link" onClick={() => requestEvidence(session)}>{session.id}</button></td>
                  <td>{session.timestamp}</td>
                  <td><span className="tier-chip">{session.tier}</span></td>
                  <td><span className="confidence-cell">{session.confidence.toFixed(1)}%</span></td>
                  <td><span className="device-cell"><MonitorCheck size={14} />{session.terminalId}</span></td>
                  <td><StatusBadge status={session.status} /></td>
                  <td><button className="icon-button row-action" aria-label={`Request evidence for ${session.id}`} onClick={() => requestEvidence(session)}><Ellipsis size={17} /></button></td>
                </tr>
              ))}
              {sessions.length === 0 && <tr><td colSpan={7} className="empty-table">No records match these filters.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="table-footer">
          <span>Showing {sessions.length ? 1 : 0}–{sessions.length} of {sessions.length} sample events</span>
          <div><button className="pagination-button" disabled><ChevronLeft size={15} /> Previous</button><button className="pagination-button" disabled>Next <ChevronRight size={15} /></button></div>
        </div>
      </section>
      <div className="compliance-note"><LockKeyhole size={15} /> Evidence audit trail preview · encryption key management and object retention are not connected in this demo.</div>
    </>
  );
}

function AnalyticsPage({ sessions }: { sessions: Session[] }) {
  const total = sessions.length;
  const denied = sessions.filter((session) => session.status === "DENY").length;
  const avgLatency = total ? sessions.reduce((sum, item) => sum + item.latency, 0) / total : 0;
  const blockedRate = total ? (denied / total) * 100 : 0;
  const bars = [42, 58, 49, 74, 62, 83, 66, 94, 72, 81, 64, 89];
  const distribution = [
    { label: "Tier 1 · Passive", value: 72, count: 432, className: "tier-one" },
    { label: "Tier 2 · Step-up", value: 21, count: 126, className: "tier-two" },
    { label: "Tier 3 · Active", value: 7, count: 42, className: "tier-three" },
  ];

  return (
    <>
      <SectionHeader
        eyebrow="OPERATIONS / PERFORMANCE"
        title="Analytics & latency"
        description="Operational overview using sample values. Connect a telemetry source to replace this illustrative dashboard."
        action={<button className="button button-secondary"><Clock3 size={15} /> Last 24 hours <ChevronDown size={14} /></button>}
      />
      <div className="notice notice-info compact-notice"><AlertTriangle size={16} /><span>Illustrative analytics · metrics are calculated from the sample/demo session data in this browser.</span></div>
      <div className="metrics-grid">
        <MetricCard label="Total verifications" value={total.toLocaleString()} change="+12.8%" icon={Fingerprint} footnote="vs. previous period" />
        <MetricCard label="Spoof block rate" value={`${blockedRate.toFixed(1)}%`} change="+2.4%" icon={ShieldAlert} iconClass="icon-rose" footnote="sample denials / sessions" />
        <MetricCard label="Average server latency" value={`${avgLatency.toFixed(2)}s`} icon={Zap} iconClass="icon-amber" footnote="Target < 1.20 s · simulated" />
        <MetricCard label="Step-up rate" value="28.0%" change="-1.6%" icon={Activity} iconClass="icon-violet" footnote="Tier 2 and Tier 3 combined" />
      </div>
      <div className="analytics-grid">
        <section className="card chart-card">
          <div className="chart-heading">
            <div><h2>Verification volume</h2><p>Sample session throughput over the last 12 hours</p></div>
            <span className="chart-legend"><i /> Verifications</span>
          </div>
          <div className="bar-chart" role="img" aria-label="Illustrative bar chart showing verification volume over 12 hours">
            {bars.map((height, index) => (
              <div className="bar-column" key={`${height}-${index}`}>
                <span className="bar-value" style={{ height: `${height}%` }} />
                <span className="bar-label">{index % 2 === 0 ? `${String(8 + index / 2).padStart(2, "0")}:00` : ""}</span>
              </div>
            ))}
          </div>
          <div className="chart-axis"><span>8:00 AM</span><span>12:00 PM</span><span>7:00 PM</span></div>
        </section>
        <section className="card tier-card">
          <div className="chart-heading">
            <div><h2>Tier escalation</h2><p>Distribution of sample checks</p></div>
            <button className="icon-button" aria-label="Tier chart options"><MoreHorizontal size={18} /></button>
          </div>
          <div className="tier-total"><strong>600</strong><span>sample sessions</span></div>
          <div className="tier-distribution">
            {distribution.map((item) => (
              <div className="tier-distribution-row" key={item.label}>
                <div className="distribution-label"><span className={`distribution-dot ${item.className}`} />{item.label}<strong>{item.value}%</strong></div>
                <div className="distribution-track"><span className={item.className} style={{ width: `${item.value}%` }} /></div>
                <span className="distribution-count">{item.count} checks</span>
              </div>
            ))}
          </div>
        </section>
      </div>
      <section className="card latency-card">
        <div><div className="chart-heading"><div><h2>Latency SLO</h2><p>Decision time budget for the configured service</p></div></div></div>
        <div className="latency-slo-row">
          <div className="slo-stat"><strong>{avgLatency.toFixed(2)}s</strong><span>Sample average</span></div>
          <div className="slo-progress"><div className="slo-track"><span style={{ width: `${Math.min((avgLatency / 1.2) * 100, 100)}%` }} /></div><div><span>0s</span><strong>Target &lt; 1.20s</strong><span>2.0s</span></div></div>
          <span className="status-badge status-verify">Telemetry not connected</span>
        </div>
      </section>
    </>
  );
}

function PoliciesPage({
  confidenceThreshold,
  setConfidenceThreshold,
  passiveBurst,
  setPassiveBurst,
  otpRequired,
  setOtpRequired,
  policySaved,
  savePolicies,
}: {
  confidenceThreshold: number;
  setConfidenceThreshold: (value: number) => void;
  passiveBurst: boolean;
  setPassiveBurst: (value: boolean) => void;
  otpRequired: boolean;
  setOtpRequired: (value: boolean) => void;
  policySaved: boolean;
  savePolicies: () => void;
}) {
  return (
    <>
      <SectionHeader
        eyebrow="ADMINISTRATION / GOVERNANCE"
        title="Security policies"
        description="Review demo policy controls. Changes are held in local UI state and are not deployed to a security service."
        action={<button className="button button-primary" onClick={savePolicies}><Check size={15} /> Save policy draft</button>}
      />
      {policySaved && <div className="notice notice-success"><CheckCircle2 size={16} /><span>Policy draft saved in this browser session. No service configuration was changed.</span></div>}
      <div className="notice notice-info compact-notice"><AlertTriangle size={16} /><span>Configuration preview only · production values require backend policy deployment and administrator approval.</span></div>

      <div className="policy-layout">
        <div className="policy-main">
          <section className="card policy-card">
            <div className="policy-section-heading">
              <span className="policy-icon"><Fingerprint size={18} /></span>
              <div><h2>Tier 1 · Passive liveness</h2><p>Configure initial capture confidence and passive analysis behavior.</p></div>
              <span className="policy-tier">TIER 1</span>
            </div>
            <div className="policy-field">
              <div className="field-heading"><label htmlFor="confidence-range">Minimum confidence threshold</label><strong>{confidenceThreshold}%</strong></div>
              <p>Below this score, the session is routed to a step-up verification tier.</p>
              <input id="confidence-range" type="range" min="60" max="99" value={confidenceThreshold} onChange={(event) => setConfidenceThreshold(Number(event.target.value))} />
              <div className="range-labels"><span>60% · More step-ups</span><span>99% · Stricter</span></div>
            </div>
            <PolicyToggle
              title="Passive burst capture"
              description="Use a short sequence of frames for passive liveness analysis."
              enabled={passiveBurst}
              onChange={setPassiveBurst}
            />
            <PolicyToggle
              title="Screen replay heuristics"
              description="Include display and temporal inconsistency signals in risk evaluation."
              enabled
              onChange={() => undefined}
              disabled
            />
          </section>

          <section className="card policy-card">
            <div className="policy-section-heading">
              <span className="policy-icon policy-icon-amber"><KeyRound size={18} /></span>
              <div><h2>Multi-Factor Identity Resolution</h2><p>Set the review path when passive signals require additional evidence.</p></div>
              <span className="policy-tier">TIER 2 / 3</span>
            </div>
            <PolicyToggle
              title="Require OTP during step-up"
              description="Add a one-time passcode requirement after an elevated risk result."
              enabled={otpRequired}
              onChange={setOtpRequired}
            />
            <div className="policy-field split-field">
              <div><label htmlFor="otp-attempts">Maximum OTP attempts</label><p>Demo lockout threshold before operator review.</p></div>
              <select id="otp-attempts" defaultValue="3"><option>3</option><option>4</option><option>5</option></select>
            </div>
            <div className="policy-field split-field">
              <div><label htmlFor="escalation-route">Escalation route</label><p>Default action when confidence is below threshold.</p></div>
              <select id="escalation-route" defaultValue="Tier 2 challenge"><option>Tier 2 challenge</option><option>Tier 3 liveness</option><option>Manual review</option></select>
            </div>
          </section>

          <section className="card policy-card">
            <div className="policy-section-heading">
              <span className="policy-icon policy-icon-rose"><Shield size={18} /></span>
              <div><h2>Edge & WAF controls</h2><p>Request protection controls shown for policy review.</p></div>
              <span className="policy-tier">NETWORK</span>
            </div>
            <div className="policy-field split-field">
              <div><label htmlFor="rate-limit">Request rate limit</label><p>Per identity node · requests per minute (demo value).</p></div>
              <div className="input-suffix"><input id="rate-limit" type="number" defaultValue={60} min={1} /><span>req/min</span></div>
            </div>
            <PolicyToggle
              title="WAF managed rules"
              description="AWS WAF is not connected; this is a non-operational policy placeholder."
              enabled={false}
              onChange={() => undefined}
              disabled
            />
          </section>
        </div>

        <aside className="policy-aside">
          <div className="card policy-summary">
            <div className="aside-heading"><h2>Policy summary</h2><span className="draft-badge">DRAFT</span></div>
            <div className="summary-row"><span>Policy version</span><strong>Draft · local only</strong></div>
            <div className="summary-row"><span>Updated</span><strong>Unsaved changes</strong></div>
            <div className="summary-row"><span>Approval status</span><StatusBadge status="VERIFY" /></div>
            <div className="summary-divider" />
            <div className="audit-trail-title"><FileClock size={15} /> Change history</div>
            <p className="summary-empty">Changes made here are not persisted to a server or audit log.</p>
          </div>
          <div className="policy-warning">
            <AlertTriangle size={17} />
            <strong>Production safety</strong>
            <p>Do not use these controls as a substitute for reviewed security policy, backend enforcement, or change approvals.</p>
          </div>
        </aside>
      </div>
    </>
  );
}

function PolicyToggle({
  title,
  description,
  enabled,
  onChange,
  disabled = false,
}: {
  title: string;
  description: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className={`policy-toggle-row ${disabled ? "toggle-disabled" : ""}`}>
      <div><strong>{title}</strong><span>{description}</span></div>
      <button
        type="button"
        className={`toggle ${enabled ? "toggle-on" : ""}`}
        role="switch"
        aria-checked={enabled}
        aria-label={title}
        disabled={disabled}
        onClick={() => onChange(!enabled)}
      >
        <span />
      </button>
    </div>
  );
}

export default App;
