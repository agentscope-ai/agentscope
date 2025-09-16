import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";

interface EvidenceSnippet {
  note: string;
  span: string;
  quote: string;
  quote_sha?: string;
}

interface CandidatePayload {
  id: string;
  subject: string;
  predicate: string;
  object: string;
  claim: string;
  explain: string;
  confidence: number;
  event_time?: string;
  valid_from?: string;
  valid_to?: string | null;
  evidence: EvidenceSnippet[];
  scores: Record<string, number>;
  degraded: boolean;
}

interface CanvasResponse {
  run_id: string;
  candidate?: CandidatePayload;
  guidance?: string;
  budget_used: number;
  degraded: boolean;
}

interface DecideResponse {
  ok: number;
  failed: string[];
  undo_expires_at?: string;
}

interface AuditArtifact {
  step: string;
  path: string;
}

interface AuditResponse {
  relation_id: string;
  run_id: string;
  status: string;
  created_at: string;
  prompt_hash?: string | null;
  input_hash?: string | null;
  cost_cents?: number | null;
  artifacts: AuditArtifact[];
  notes?: string | null;
}

const predicateLabels: Record<string, string> = {
  supports: "支持",
  refutes: "反驳",
  causes: "导致",
  cites: "引用"
};

const formatPercent = (value: number) => `${Math.round(value * 100)}%`;

const formatDateTime = (value?: string) => {
  if (!value) return "";
  const date = new Date(value);
  return date.toLocaleString();
};

const ScoreBar = ({ scores }: { scores: Record<string, number> }) => {
  const entries = Object.entries(scores);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
      {entries.map(([key, value]) => (
        <div
          key={key}
          style={{
            background: "#eef2ff",
            borderRadius: 8,
            padding: "8px 12px",
            minWidth: 88
          }}
        >
          <div style={{ fontSize: 12, color: "#6366f1", textTransform: "uppercase" }}>{key}</div>
          <div style={{ fontWeight: 600 }}>{formatPercent(value)}</div>
        </div>
      ))}
    </div>
  );
};

const EvidenceBlock = ({ snippet }: { snippet: EvidenceSnippet }) => (
  <div
    style={{
      background: "#fff6d0",
      borderRadius: 12,
      padding: "12px 16px",
      marginTop: 12
    }}
  >
    <div style={{ fontSize: 12, color: "#6b7280" }}>
      {snippet.note} · {snippet.span}
    </div>
    <div style={{ marginTop: 4, fontSize: 14, lineHeight: 1.5 }}>{snippet.quote}</div>
  </div>
);

const AuditModal = ({ audit, onClose }: { audit: AuditResponse; onClose: () => void }) => (
  <div
    style={{
      position: "fixed",
      inset: 0,
      background: "rgba(28,28,30,0.55)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: 24
    }}
    role="dialog"
    aria-modal="true"
  >
    <div
      style={{
        background: "#ffffff",
        borderRadius: 24,
        padding: 32,
        maxWidth: 600,
        width: "100%",
        maxHeight: "85vh",
        overflowY: "auto"
      }}
    >
      <h2 style={{ fontSize: 24, fontWeight: 600, marginBottom: 8 }}>审计详情</h2>
      <p style={{ color: "#4b5563", marginBottom: 16 }}>Relation ID: {audit.relation_id}</p>
      <p style={{ color: "#4b5563", marginBottom: 8 }}>Run ID: {audit.run_id}</p>
      <p style={{ color: "#4b5563", marginBottom: 8 }}>状态: {audit.status}</p>
      <p style={{ color: "#4b5563", marginBottom: 8 }}>生成时间: {formatDateTime(audit.created_at)}</p>
      <div style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>生成步骤</h3>
        <ul>
          {audit.artifacts.map((artifact) => (
            <li key={artifact.path} style={{ marginBottom: 6, color: "#1c1c1e" }}>
              {artifact.step}: <code>{artifact.path}</code>
            </li>
          ))}
        </ul>
      </div>
      {audit.notes && <p style={{ color: "#6b7280", marginTop: 12 }}>{audit.notes}</p>}
      <button
        onClick={onClose}
        style={{
          marginTop: 24,
          background: "#0b84ff",
          color: "#ffffff",
          border: "none",
          padding: "12px 24px",
          borderRadius: 999,
          fontWeight: 600,
          cursor: "pointer"
        }}
      >
        关闭
      </button>
    </div>
  </div>
);

const ConnectionCard = ({
  candidate,
  onVerify,
  onSwap,
  onAudit
}: {
  candidate: CandidatePayload;
  onVerify: () => void;
  onSwap: () => void;
  onAudit: () => void;
}) => {
  const label = predicateLabels[candidate.predicate] ?? candidate.predicate;
  return (
    <div
      style={{
        background: "#ffffff",
        borderRadius: 24,
        padding: 32,
        boxShadow: "0 24px 48px rgba(12, 15, 30, 0.08)",
        maxWidth: 760,
        margin: "32px auto"
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span
          style={{
            padding: "6px 12px",
            borderRadius: 999,
            background: "#ebf5ff",
            color: "#0b84ff",
            fontWeight: 600
          }}
        >
          {label}
        </span>
        <span style={{ color: "#6b7280", fontSize: 14 }}>
          置信度 {candidate.confidence.toFixed(2)}
        </span>
        {candidate.degraded && (
          <span style={{ color: "#b45309", fontSize: 12 }}>Degraded</span>
        )}
      </div>
      <h2 style={{ fontWeight: 600, fontSize: 26, marginTop: 24 }}>{candidate.claim}</h2>
      <p style={{ fontSize: 16, color: "#374151" }}>{candidate.explain}</p>
      {candidate.evidence.map((snippet) => (
        <EvidenceBlock key={`${snippet.note}-${snippet.span}`} snippet={snippet} />
      ))}
      <ScoreBar scores={candidate.scores} />
      <div style={{ marginTop: 12, color: "#6b7280", fontSize: 14 }}>
        首次出现：{formatDateTime(candidate.event_time)} · 有效期开始：{formatDateTime(candidate.valid_from)}
      </div>
      <div
        style={{
          display: "flex",
          gap: 16,
          marginTop: 32,
          justifyContent: "flex-start"
        }}
      >
        <button
          onClick={onVerify}
          style={{
            background: "#2db47c",
            color: "white",
            border: "none",
            padding: "14px 24px",
            borderRadius: 999,
            cursor: "pointer",
            fontSize: 16,
            fontWeight: 600
          }}
        >
          采纳此连接
        </button>
        <button
          onClick={onSwap}
          style={{
            background: "#ffffff",
            color: "#0b84ff",
            border: "1px solid #0b84ff",
            padding: "14px 24px",
            borderRadius: 999,
            cursor: "pointer",
            fontSize: 16,
            fontWeight: 600
          }}
        >
          换一个惊喜
        </button>
        <button
          onClick={onAudit}
          style={{
            background: "transparent",
            color: "#4b5563",
            border: "none",
            padding: "14px 12px",
            cursor: "pointer",
            fontSize: 16,
            textDecoration: "underline"
          }}
        >
          查看审计
        </button>
      </div>
    </div>
  );
};

const App = () => {
  const [content, setContent] = useState("");
  const [candidate, setCandidate] = useState<CandidatePayload | undefined>();
  const [lastRunId, setLastRunId] = useState<string | undefined>();
  const [guidance, setGuidance] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [undoUntil, setUndoUntil] = useState<Date | undefined>();
  const [undoCandidate, setUndoCandidate] = useState<CandidatePayload | undefined>();
  const [auditData, setAuditData] = useState<AuditResponse | undefined>();
  const [error, setError] = useState<string | undefined>();

  const canSubmit = content.trim().length > 0 && !loading;
  const undoSecondsLeft = useMemo(() => {
    if (!undoUntil) return 0;
    const diff = undoUntil.getTime() - Date.now();
    return diff > 0 ? Math.ceil(diff / 1000) : 0;
  }, [undoUntil]);

  useEffect(() => {
    if (!undoUntil) return;
    if (undoSecondsLeft <= 0) {
      setUndoCandidate(undefined);
      setUndoUntil(undefined);
      return;
    }
    const timer = setInterval(() => {
      const remaining = undoUntil.getTime() - Date.now();
      if (remaining <= 0) {
        setUndoCandidate(undefined);
        setUndoUntil(undefined);
      }
    }, 500);
    return () => clearInterval(timer);
  }, [undoUntil, undoSecondsLeft]);

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;
    setError(undefined);
    try {
      setLoading(true);
      const response = await axios.post<CanvasResponse>("/canvas/submit", {
        content,
        note_id: "Z_canvas_input"
      });
      setCandidate(response.data.candidate);
      setGuidance(response.data.guidance ?? undefined);
      setLastRunId(response.data.run_id);
    } catch (err) {
      setError("生成连接失败，请稍后再试。" + (err instanceof Error ? err.message : ""));
    } finally {
      setLoading(false);
    }
  }, [canSubmit, content]);

  const handleVerify = useCallback(async () => {
    if (!candidate) return;
    setError(undefined);
    try {
      const response = await axios.post<DecideResponse>("/relations/decide", {
        ops: [{ id: candidate.id, action: "verify" }]
      });
      if (response.data.undo_expires_at) {
        setUndoUntil(new Date(response.data.undo_expires_at));
        setUndoCandidate(candidate);
      }
      setGuidance("已采纳。继续写下一个念头吧。");
      setCandidate(undefined);
      setContent("");
    } catch (err) {
      setError("采纳失败，请重试。" + (err instanceof Error ? err.message : ""));
    }
  }, [candidate]);

  const handleUndo = useCallback(async () => {
    if (!undoCandidate) return;
    try {
      await axios.post<DecideResponse>("/relations/decide", {
        ops: [{ id: undoCandidate.id, action: "undo" }]
      });
      setCandidate(undoCandidate);
    } finally {
      setUndoCandidate(undefined);
      setUndoUntil(undefined);
    }
  }, [undoCandidate]);

  const handleSwap = useCallback(async () => {
    if (!candidate) return;
    setError(undefined);
    try {
      const response = await axios.post<{ candidate?: CandidatePayload; degraded: boolean }>(
        "/relations/swap",
        {
          id: candidate.id,
          subject: candidate.subject,
          predicate: candidate.predicate
        }
      );
      if (response.data.candidate) {
        setCandidate(response.data.candidate);
      }
    } catch (err) {
      setError("暂时无法换一个惊喜。" + (err instanceof Error ? err.message : ""));
    }
  }, [candidate]);

  const handleAudit = useCallback(async () => {
    if (!candidate) return;
    try {
      const response = await axios.get<AuditResponse>(`/audit/${candidate.id}`);
      setAuditData(response.data);
    } catch (err) {
      setError("获取审计信息失败。" + (err instanceof Error ? err.message : ""));
    }
  }, [candidate]);

  return (
    <div style={{ padding: "64px 24px", minHeight: "100vh" }}>
      <header style={{ maxWidth: 760, margin: "0 auto" }}>
        <h1 style={{ fontSize: 32, fontWeight: 600 }}>Relation-Zettel 顿悟工具</h1>
        <p style={{ color: "#4b5563", marginTop: 8 }}>
          写下一句念头，系统递上一条带证据的连接。
        </p>
      </header>

      <section
        style={{
          maxWidth: 760,
          margin: "48px auto 0",
          background: "#ffffff",
          borderRadius: 24,
          padding: 32,
          boxShadow: "0 16px 36px rgba(12, 15, 30, 0.06)"
        }}
      >
        <label htmlFor="canvas-input" style={{ display: "block", fontSize: 14, color: "#6b7280" }}>
          写下一个念头、一段引文，或贴一个链接…
        </label>
        <textarea
          id="canvas-input"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          rows={5}
          style={{
            width: "100%",
            marginTop: 12,
            padding: 16,
            borderRadius: 16,
            border: "1px solid #d1d5db",
            fontSize: 16,
            resize: "vertical"
          }}
          placeholder="写下你的想法，这里始终是白纸。"
        />
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 16, alignItems: "center" }}>
          <span style={{ fontSize: 14, color: "#6b7280" }}>
            {loading ? "正在倾听你的思路…" : guidance ?? "按 Enter 或点击按钮生成连接"}
          </span>
          <button
            disabled={!canSubmit}
            onClick={handleSubmit}
            style={{
              background: canSubmit ? "#0b84ff" : "#bfdbfe",
              color: "white",
              border: "none",
              padding: "12px 24px",
              borderRadius: 999,
              cursor: canSubmit ? "pointer" : "not-allowed",
              fontSize: 16,
              fontWeight: 600
            }}
          >
            生成连接
          </button>
        </div>
        {error && (
          <div style={{ color: "#b91c1c", marginTop: 12, fontSize: 14 }}>{error}</div>
        )}
      </section>

      {undoCandidate && undoSecondsLeft > 0 && (
        <div
          style={{
            maxWidth: 760,
            margin: "24px auto 0",
            padding: "16px 24px",
            background: "#ecfdf5",
            borderRadius: 16,
            color: "#047857",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center"
          }}
        >
          <span>
            已采纳「{undoCandidate.claim}」。
            <strong> {undoSecondsLeft} 秒内可撤销。</strong>
          </span>
          <button
            onClick={handleUndo}
            style={{
              background: "transparent",
              color: "#047857",
              border: "1px solid #047857",
              padding: "8px 16px",
              borderRadius: 999,
              cursor: "pointer",
              fontWeight: 600
            }}
          >
            撤销
          </button>
        </div>
      )}

      {candidate && (
        <ConnectionCard
          candidate={candidate}
          onVerify={handleVerify}
          onSwap={handleSwap}
          onAudit={handleAudit}
        />
      )}

      <footer style={{ maxWidth: 760, margin: "64px auto", color: "#9ca3af", fontSize: 12 }}>
        run id: {lastRunId ?? "-"}
      </footer>

      {auditData && <AuditModal audit={auditData} onClose={() => setAuditData(undefined)} />}
    </div>
  );
};

export default App;
