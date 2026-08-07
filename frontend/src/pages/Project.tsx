import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, type Project as Proj, type PhaseSpec, type RunState } from "../api";
import Layout from "../components/Layout";
import PipelinePhaseRoadmap from "../components/PipelinePhaseRoadmap";
import PhaseOutputPanel from "../components/PhaseOutputPanel";
import VersionHistory from "../components/VersionHistory";
import WritingEditor from "../components/WritingEditor";
import PipelineChat from "../components/PipelineChat";
import MarkdownViewer from "../components/MarkdownViewer";

type Tab = "pipeline" | "write" | "outputs" | "versions";
const TABS: { key: Tab; label: string }[] = [
  { key: "pipeline", label: "Pipeline" },
  { key: "write", label: "Write" },
  { key: "outputs", label: "Outputs" },
  { key: "versions", label: "Versions" },
];

// Auto-run log entry shape (mirrors the backend auto-run/status payload).
type LogEntry = { time: string; message: string; type: "info" | "warn" | "error" };

export default function Project() {
  const { id = "" } = useParams();
  const [project, setProject] = useState<Proj | null>(null);
  const [phases, setPhases] = useState<PhaseSpec[]>([]);
  const [state, setState] = useState<RunState | null>(null);
  const [tab, setTab] = useState<Tab>("pipeline");
  const [error, setError] = useState("");

  // ── Server-side auto-run state (driven by 3s polling) ────────────────────
  const [autoRunning, setAutoRunning] = useState(false);
  const [autoRunLog, setAutoRunLog] = useState<LogEntry[]>([]);
  const [autoRunFailed, setAutoRunFailed] = useState(false);
  const [autoRunFailedPhase, setAutoRunFailedPhase] = useState("");
  const [autoCountdown, setAutoCountdown] = useState(0);

  // ── Single-phase background execution (advance-phase) ────────────────────
  // Lifted to Project level so the 3s poll can detect completion on any tab.
  const [phaseExecuting, setPhaseExecuting] = useState(false);
  const [phaseResult, setPhaseResult] = useState<Record<string, unknown> | null>(null);
  const phaseExecutingRef = useRef(false);
  const prevPhaseRef = useRef("");   // current_phase at the moment advance was submitted
  const prevErrorRef = useRef("");   // last_error at the moment advance was submitted

  // Called by PipelineTab right after a phase is submitted to the backend.
  const beginPhaseWatch = useCallback((phase: string, lastError: string) => {
    prevPhaseRef.current = phase;
    prevErrorRef.current = lastError;
    phaseExecutingRef.current = true;
    setPhaseExecuting(true);
  }, []);

  const refreshState = useCallback(() => {
    api.runState(id).then((s) => setState(s.active ? s : null)).catch(() => setState(null));
  }, [id]);

  useEffect(() => {
    api.getProject(id).then(setProject).catch((e) => setError(e.message));
    api.phaseOrder(id).then((r) => setPhases(r.phases)).catch(() => {});
    refreshState();
  }, [id, refreshState]);

  // Poll pipeline state and server-side auto-run status every 3 seconds.
  // Runs regardless of the active tab so progress stays live.
  useEffect(() => {
    const tick = async () => {
      let fresh: RunState | null = null;
      try {
        const s = await api.runState(id);
        fresh = s.active ? s : null;
        setState(fresh);
      } catch { /* ignore transient errors */ }

      // Detect completion of a background advance-phase: the current phase
      // advanced, or a new error appeared, since we submitted it.
      if (phaseExecutingRef.current) {
        const cur = fresh?.current_phase || "";
        const err = fresh?.last_error || "";
        if (cur !== prevPhaseRef.current || err !== prevErrorRef.current) {
          phaseExecutingRef.current = false;
          setPhaseExecuting(false);
          try {
            const r = await api.phaseTaskResult(id);
            if (r && Object.keys(r).length > 0) setPhaseResult(r);
          } catch { /* ignore */ }
        }
      }

      try {
        const ar = await api.autoRunStatus(id);
        setAutoRunning(ar.running);
        setAutoRunLog(ar.log);
        setAutoRunFailed(ar.failed);
        setAutoRunFailedPhase(ar.failed_phase);
        setAutoCountdown(ar.countdown);
      } catch { /* ignore transient errors */ }
    };
    const iv = setInterval(tick, 3000);
    return () => clearInterval(iv);
  }, [id]);

  return (
    <Layout>
      {error && <div className="mb-4 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <div className="mb-5">
        <Link to="/" className="text-xs text-gray-500 hover:text-gray-300">← All projects</Link>
        <div className="mt-1 flex items-center gap-3">
          <h1 className="text-2xl font-semibold text-gray-100">{project?.name || "Project"}</h1>
          {project && <span className="badge bg-ink-800 text-gray-400 capitalize">{project.format}</span>}
        </div>
        {project?.description && <p className="mt-1 text-sm text-gray-500">{project.description}</p>}
      </div>

      <div className="mb-6 border-b border-edge">
        <div className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={[
                "border-b-2 px-4 py-2.5 text-sm font-medium transition-colors",
                tab === t.key
                  ? "border-accent-soft text-gray-100"
                  : "border-transparent text-gray-500 hover:text-gray-300",
              ].join(" ")}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "pipeline" && (
        <PipelineTab
          projectId={id}
          phases={phases}
          state={state}
          onChange={refreshState}
          autoRunning={autoRunning}
          autoRunLog={autoRunLog}
          autoRunFailed={autoRunFailed}
          autoRunFailedPhase={autoRunFailedPhase}
          autoCountdown={autoCountdown}
          phaseExecuting={phaseExecuting}
          phaseResult={phaseResult}
          onBeginPhaseWatch={beginPhaseWatch}
          onStartAutoRun={async (instructions: string) => {
            const res = await api.startAutoRun(id, instructions);
            if (!res.started) {
              throw new Error("Auto-run is already active for this project. Stop it first, then retry.");
            }
            // Optimistically flip so the UI switches to the running state
            // immediately, without waiting for the next 3s poll.
            setAutoRunning(true);
            setAutoRunFailed(false);
          }}
          onStopAutoRun={async () => {
            await api.stopAutoRun(id);
            setAutoRunning(false);
          }}
        />
      )}
      {tab === "write" && <WritingEditor projectId={id} />}
      {tab === "outputs" && <PhaseOutputPanel projectId={id} />}
      {tab === "versions" && <VersionHistory projectId={id} />}
    </Layout>
  );
}

const fmtMMSS = (total: number) => {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
};

// ── Pipeline tab ────────────────────────────────────────────────────────────
function PipelineTab({
  projectId,
  phases,
  state,
  onChange,
  autoRunning,
  autoRunLog,
  autoRunFailed,
  autoRunFailedPhase,
  autoCountdown,
  phaseExecuting,
  phaseResult,
  onBeginPhaseWatch,
  onStartAutoRun,
  onStopAutoRun,
}: {
  projectId: string;
  phases: PhaseSpec[];
  state: RunState | null;
  onChange: () => void;
  autoRunning: boolean;
  autoRunLog: LogEntry[];
  autoRunFailed: boolean;
  autoRunFailedPhase: string;
  autoCountdown: number;
  phaseExecuting: boolean;
  phaseResult: Record<string, unknown> | null;
  onBeginPhaseWatch: (phase: string, lastError: string) => void;
  onStartAutoRun: (instructions: string) => Promise<void>;
  onStopAutoRun: () => Promise<void>;
}) {
  const [instructions, setInstructions] = useState("");
  const [wordFloor, setWordFloor] = useState(800);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null);
  const [artifact, setArtifact] = useState<string>("");
  const [showLog, setShowLog] = useState(false);
  const [maxChapterRetries, setMaxChapterRetries] = useState(state?.max_chapter_retries ?? 2);
  const [maxEditorialLockRetries, setMaxEditorialLockRetries] = useState(state?.max_editorial_lock_retries ?? 2);
  const [showResetModal, setShowResetModal] = useState(false);

  useEffect(() => {
    if (state?.instructions) setInstructions(state.instructions);
    if (state?.max_chapter_retries) setMaxChapterRetries(state.max_chapter_retries);
    if (state?.max_editorial_lock_retries !== undefined) setMaxEditorialLockRetries(state.max_editorial_lock_retries);
  }, [state?.instructions, state?.max_chapter_retries, state?.max_editorial_lock_retries]);

  const start = async () => {
    setBusy(true); setError(""); setLastResult(null);
    try {
      // Stop any active server-side auto-run before a fresh start.
      await onStopAutoRun().catch(() => {});
      await api.startRun(projectId, { instructions, word_floor: wordFloor, rerun_mode: "fresh", max_chapter_retries: maxChapterRetries, max_editorial_lock_retries: maxEditorialLockRetries });
      onChange();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const advance = async () => {
    setBusy(true); setError("");
    try {
      // Non-blocking: the backend runs the phase server-side and returns
      // immediately (long phases can exceed the hosting proxy's HTTP timeout).
      await api.advancePhase(projectId, { instructions });
      // Watch for completion at the Project level so it survives tab switches.
      onBeginPhaseWatch(state?.current_phase || "", state?.last_error || "");
      onChange();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  // When a background phase completes, the Project-level poll stores its result
  // here. Surface it as the "last phase" preview, or show its error.
  useEffect(() => {
    if (!phaseResult) return;
    if (typeof phaseResult.error === "string" && phaseResult.error) {
      setError(phaseResult.error);
      return;
    }
    setLastResult(phaseResult);
    setArtifact("");
    const result = (phaseResult.result as Record<string, unknown>) || {};
    const path = (result.artifact as string) ||
      ((result.artifacts as string[] | undefined)?.[0]) ||
      (result.chapter as string);
    if (typeof path === "string" && path) {
      api.outputFile(projectId, path)
        .then((f) => setArtifact(f.content || ""))
        .catch(() => { /* ignore preview errors */ });
    }
  }, [phaseResult, projectId]);

  const startAuto = async () => {
    setError("");
    try {
      await onStartAutoRun(instructions);
      setShowLog(true);
    } catch (e) { setError((e as Error).message); }
  };

  const active = !!state?.active;
  const done = active && !state?.current_phase;
  const currentPhaseLabel = state?.current_phase_label || state?.current_phase || "";
  const capturedVersions = (lastResult?.captured_versions as string[]) || [];

  // Load editorial reports once the pipeline is complete so the revision panel
  // can show per-chapter editorial snippets alongside the chapter checkboxes.
  const [editorialReports, setEditorialReports] = useState<
    { chapter: number; content: string | null }[]
  >([]);
  useEffect(() => {
    if (!done) return;
    api.editorialReports(projectId)
      .then((r) => setEditorialReports(r.reports))
      .catch(() => { /* ignore — panel still lists chapters */ });
  }, [done, projectId]);

  const showRevision = done || editorialReports.length > 0;

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <div className="card p-5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-300">Pipeline status</h3>
            <div className="flex items-center gap-2">
              {active && (
                <span className="badge bg-accent-soft/15 text-accent">
                  {state?.current_phase_label || state?.current_phase || (done ? "Complete" : "Running")}
                </span>
              )}
              {active && state?.current_phase === "editorial_lock" && (state?.editorial_lock_retries ?? 0) > 0 && (
                <span className="badge bg-amber-600/15 text-amber-300">
                  Rev {state.editorial_lock_retries}/{state.max_editorial_lock_retries}
                </span>
              )}
              {!active && <span className="badge bg-ink-800 text-gray-500">Not started</span>}
            </div>
          </div>

          {phases.length > 0 && <PipelinePhaseRoadmap phases={phases} state={state} />}

          {state?.last_error && (
            <div className="mt-4 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{state.last_error}</div>
          )}
          {error && <div className="mt-4 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}

          {autoRunFailed && (
            <div className="mt-4 flex items-center justify-between gap-3 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">
              <span>🔴 Auto-run stopped after 4 failed attempts on "{autoRunFailedPhase}".</span>
              <button className="btn-ghost !py-1 text-xs" onClick={() => setShowLog(true)}>View Log</button>
            </div>
          )}

          <div className="mt-5">
            <label className="mb-1 block text-xs text-gray-400">Creative brief / instructions</label>
            <textarea
              className="input h-32 resize-y"
              placeholder="Describe the story you want to write — premise, characters, tone, setting, constraints…"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              disabled={busy}
            />
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            {!active && (
              <>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-400">Word floor</label>
                  <input
                    type="number" className="input w-24"
                    value={wordFloor} onChange={(e) => setWordFloor(Number(e.target.value) || 0)}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-400">Revision rounds</label>
                  <input
                    type="number" className="input w-20"
                    min={1} max={10}
                    value={maxChapterRetries}
                    onChange={(e) => setMaxChapterRetries(Number(e.target.value) || 1)}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-400">Editorial revisions</label>
                  <input
                    type="number" className="input w-20"
                    min={0} max={5}
                    value={maxEditorialLockRetries}
                    onChange={(e) => setMaxEditorialLockRetries(Number(e.target.value) || 0)}
                  />
                </div>
                <button className="btn-primary" onClick={start} disabled={busy || !instructions.trim()}>
                  {busy ? "Starting…" : "Start pipeline"}
                </button>
              </>
            )}
            {active && !done && !autoRunning && phaseExecuting && (
              <span className="flex items-center gap-2 text-sm text-accent">
                <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
                Phase executing{currentPhaseLabel ? ` · ${currentPhaseLabel}` : ""}…
              </span>
            )}
            {active && !done && !autoRunning && !phaseExecuting && (
              <>
                <button className="btn-primary" onClick={advance} disabled={busy}>
                  {busy ? "Starting…" : `Run next phase${currentPhaseLabel ? ` · ${currentPhaseLabel}` : ""}`}
                </button>
                <button className="btn-ghost" onClick={startAuto} disabled={busy}>Auto-run</button>
              </>
            )}
            {active && !done && autoRunning && (
              <div className="flex items-center gap-3">
                {autoCountdown > 0 ? (
                  <span className="flex items-center gap-2 text-sm text-amber-300">
                    <span aria-hidden>⏳</span>
                    Retrying in {fmtMMSS(autoCountdown)}…
                  </span>
                ) : (
                  <span className="flex items-center gap-2 text-sm text-accent">
                    <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
                    Auto-running{currentPhaseLabel ? ` · ${currentPhaseLabel}` : ""}
                  </span>
                )}
                <button className="btn-ghost" onClick={() => { onStopAutoRun().catch(() => {}); }}>Stop</button>
              </div>
            )}
            {done && <span className="text-sm text-emerald-400">Pipeline complete 🎉</span>}
            {autoRunLog.length > 0 && (
              <button
                className="text-xs text-gray-400 underline underline-offset-2 hover:text-gray-200"
                onClick={() => setShowLog(true)}
              >
                View Log
              </button>
            )}
            {active && <button className="btn-ghost" onClick={() => setShowResetModal(true)} disabled={busy}>Reset run</button>}
            <button
              className="btn-ghost text-sm"
              onClick={async () => {
                setError("");
                try { await api.exportProject(projectId); }
                catch (e) { setError((e as Error).message); }
              }}
            >
              ↓ Export project
            </button>
            <button
              className="btn-ghost text-sm"
              onClick={async () => {
                setError("");
                try { await api.exportFromVersions(projectId); }
                catch (e) { setError((e as Error).message); }
              }}
            >
              ↓ Recover from history
            </button>
          </div>
        </div>

        {showRevision && (
          <RevisionPanel
            units={state?.units || []}
            editorialReports={editorialReports}
            autoRunning={autoRunning}
            unitLabel={state?.unit_label || "chapter"}
            onStartRevision={async (chapters, revNotes) => {
              setError("");
              try {
                await api.startRevision(projectId, { chapters, revision_notes: revNotes });
                await onStartAutoRun(instructions);
                setShowLog(true);
                onChange();
              } catch (e) { setError((e as Error).message); }
            }}
          />
        )}

        {lastResult && (
          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <h3 className="text-sm font-semibold text-gray-300">
                Last phase: {(lastResult.phase_label as string) || (lastResult.phase as string)}
              </h3>
              {capturedVersions.length > 0 && (
                <span className="badge bg-emerald-600/15 text-emerald-300">
                  {capturedVersions.length} version{capturedVersions.length !== 1 ? "s" : ""} captured
                </span>
              )}
              {lastResult.next_phase ? (
                <span className="badge bg-ink-800 text-gray-400">next: {lastResult.next_phase as string}</span>
              ) : (
                <span className="badge bg-emerald-600/15 text-emerald-300">final phase</span>
              )}
            </div>
            {artifact ? (
              <div className="max-h-[32rem] overflow-y-auto rounded-lg bg-ink-950/50 p-4">
                <MarkdownViewer content={artifact} />
              </div>
            ) : (
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-ink-950/50 p-4 text-xs text-gray-400">
                {JSON.stringify(lastResult.result ?? lastResult, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>

      <div className="lg:col-span-1">
        <div className="h-[36rem]">
          <PipelineChat projectId={projectId} onSuggest={setInstructions} />
        </div>
      </div>

      {showLog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setShowLog(false)}
        >
          <div
            className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-edge bg-ink-900 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-edge px-5 py-3">
              <h3 className="text-sm font-semibold text-gray-200">Auto-run log</h3>
              <button
                className="btn-ghost !py-1 text-xs"
                onClick={() => setShowLog(false)}
              >
                Close
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {autoRunLog.length === 0 ? (
                <p className="text-sm text-gray-500">No log entries yet.</p>
              ) : (
                <ul className="space-y-1 font-mono text-xs">
                  {autoRunLog.map((e, i) => (
                    <li
                      key={i}
                      className={
                        e.type === "error"
                          ? "text-red-300"
                          : e.type === "warn"
                          ? "text-amber-300"
                          : "text-gray-300"
                      }
                    >
                      <span className="text-gray-500">[{e.time}]</span> {e.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}

      {showResetModal && (
        <ResetModal
          projectId={projectId}
          phases={phases}
          state={state}
          onClose={() => setShowResetModal(false)}
          onReset={() => {
            setLastResult(null);
            setArtifact("");
            onChange();
          }}
          onRewind={() => onChange()}
          onStopAutoRun={onStopAutoRun}
        />
      )}
    </div>
  );
}

// ── Revision panel ────────────────────────────────────────────────────────
function RevisionPanel({
  units,
  editorialReports,
  autoRunning,
  unitLabel,
  onStartRevision,
}: {
  units: number[];
  editorialReports: { chapter: number; content: string | null }[];
  autoRunning: boolean;
  unitLabel: string;
  onStartRevision: (chapters: number[], notes: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState<number[]>([]);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  const toggle = (ch: number) => {
    setSelected((prev) => (prev.includes(ch) ? prev.filter((c) => c !== ch) : [...prev, ch]));
  };

  const handleStart = async () => {
    if (selected.length === 0) return;
    setBusy(true);
    try {
      await onStartRevision(selected, notes);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card mt-6 border border-accent/20 bg-accent/5 p-5">
      <h3 className="mb-4 text-lg font-semibold text-gray-100">Revise project {unitLabel}s</h3>

      <div className="mb-6 space-y-3">
        {units.map((ch) => {
          const report = editorialReports.find((r) => r.chapter === ch);
          return (
            <div key={ch} className="flex flex-col border-b border-edge/50 pb-3">
              <label className="flex cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  checked={selected.includes(ch)}
                  onChange={() => toggle(ch)}
                />
                <span className="text-sm font-medium text-gray-200">{unitLabel.charAt(0).toUpperCase() + unitLabel.slice(1)} {ch}</span>
              </label>
              {report?.content && (
                <div className="mt-2 pl-7 text-xs italic text-gray-400">
                  {report.content.substring(0, 300)}…
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="space-y-4">
        <div>
          <label className="mb-1 block text-xs text-gray-400">Revision instructions (your feedback)</label>
          <textarea
            className="input h-24 resize-y text-sm"
            placeholder={`What should be changed in these ${unitLabel}s?`}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>

        <button
          className="btn-primary w-full"
          disabled={busy || selected.length === 0 || autoRunning}
          onClick={handleStart}
        >
          {busy
            ? "Starting revision…"
            : `Start revision for ${selected.length} ${selected.length === 1 ? unitLabel : unitLabel + "s"}`}
        </button>
      </div>
    </div>
  );
}


// ── Reset / Resume run modal ────────────────────────────────────────────────
function ResetModal({
  projectId, phases, state, onClose, onReset, onRewind, onStopAutoRun
}: {
  projectId: string;
  phases: PhaseSpec[];
  state: RunState | null;
  onClose: () => void;
  onReset: () => void;
  onRewind: () => void;
  onStopAutoRun: () => Promise<void>;
}) {
  const unitPhases = new Set(["architect", "writer", "critics", "editorial", "verify_unit"]);
  const [mode, setMode] = useState<"full" | "rewind">("rewind");
  const [phase, setPhase] = useState(state?.current_phase || phases[0]?.key || "");
  const [chapter, setChapter] = useState<number>((state?.current_unit_index ?? 0) + 1);
  const [maxRetries, setMaxRetries] = useState(state?.max_chapter_retries ?? 2);
  const [maxEditorialLockRetries, setMaxEditorialLockRetries] = useState(state?.max_editorial_lock_retries ?? 2);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const isUnitPhase = unitPhases.has(phase);
  const totalChapters = state?.units?.length ?? 0;
  const unitLabel = state?.unit_label || "chapter";

  const handleSubmit = async () => {
    setBusy(true);
    setError("");
    try {
      if (mode === "full") {
        await onStopAutoRun().catch(() => {});
        await api.resetRun(projectId);
        onReset();
      } else {
        await api.resetRun(projectId, {
          phase,
          chapter: isUnitPhase ? chapter : undefined,
          max_chapter_retries: maxRetries,
          max_editorial_lock_retries: maxEditorialLockRetries,
        });
        onRewind();
      }
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-edge bg-ink-900 shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-edge px-5 py-3">
          <h3 className="text-sm font-semibold text-gray-200">Reset / Resume Run</h3>
          <button className="btn-ghost !py-1 text-xs" onClick={onClose}>Close</button>
        </div>

        <div className="p-5 space-y-5">
          {/* Mode tabs */}
          <div className="flex gap-2">
            <button
              className={`flex-1 rounded-lg py-2 text-sm font-medium transition-colors ${mode === "rewind" ? "bg-accent text-white" : "bg-ink-800 text-gray-400 hover:text-gray-200"}`}
              onClick={() => setMode("rewind")}
            >
              Resume from point
            </button>
            <button
              className={`flex-1 rounded-lg py-2 text-sm font-medium transition-colors ${mode === "full" ? "bg-red-600 text-white" : "bg-ink-800 text-gray-400 hover:text-gray-200"}`}
              onClick={() => setMode("full")}
            >
              Full reset
            </button>
          </div>

          {mode === "full" ? (
            <div className="rounded-lg bg-red-600/10 border border-red-600/20 px-4 py-3 text-sm text-red-300">
              ⚠️ This deletes all pipeline progress state. Generated files remain on disk. The next run starts fresh from the beginning.
            </div>
          ) : (
            <div className="space-y-4">
              {/* Phase dropdown */}
              <div>
                <label className="mb-1 block text-xs text-gray-400">Phase to resume from</label>
                <select
                  className="input w-full"
                  value={phase}
                  onChange={e => { setPhase(e.target.value); }}
                >
                  {phases.map(p => (
                    <option key={p.key} value={p.key}>{p.label}</option>
                  ))}
                </select>
              </div>

              {/* Unit input — only for unit phases */}
              {isUnitPhase && totalChapters > 0 && (
                <div>
                  <label className="mb-1 block text-xs text-gray-400">{unitLabel.charAt(0).toUpperCase() + unitLabel.slice(1)} number (1–{totalChapters})</label>
                  <input
                    type="number"
                    className="input w-28"
                    min={1} max={totalChapters}
                    value={chapter}
                    onChange={e => setChapter(Math.max(1, Math.min(totalChapters, Number(e.target.value) || 1)))}
                  />
                </div>
              )}

              {/* Revision rounds */}
              <div>
                <label className="mb-1 block text-xs text-gray-400">Max critic revision rounds per {unitLabel}</label>
                <input
                  type="number"
                  className="input w-24"
                  min={1} max={10}
                  value={maxRetries}
                  onChange={e => setMaxRetries(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
                />
                <p className="mt-1 text-xs text-gray-500">How many times the writer revises based on critic feedback before moving on.</p>
              </div>

              {/* Editorial revision rounds */}
              <div>
                <label className="mb-1 block text-xs text-gray-400">Max editorial outline revisions</label>
                <input
                  type="number"
                  className="input w-24"
                  min={0} max={5}
                  value={maxEditorialLockRetries}
                  onChange={e => setMaxEditorialLockRetries(Math.max(0, Math.min(5, Number(e.target.value) || 0)))}
                />
                <p className="mt-1 text-xs text-gray-500">How many times the editorial review can request outline revisions before proceeding.</p>
              </div>

              <div className="rounded-lg bg-accent/10 border border-accent/20 px-4 py-3 text-xs text-accent/80">
                Prior work is preserved. The pipeline will rerun from the selected phase and {unitLabel}.
              </div>
            </div>
          )}

          {error && <div className="rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}

          <div className="flex justify-end gap-3 pt-1">
            <button className="btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
            <button
              className={mode === "full" ? "btn bg-red-600 text-white hover:bg-red-700" : "btn-primary"}
              onClick={handleSubmit}
              disabled={busy}
            >
              {busy
                ? "Working..."
                : mode === "full"
                ? "Reset everything"
                : `Resume from ${phase}${isUnitPhase ? ` · ${unitLabel} ${chapter}` : ""}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
