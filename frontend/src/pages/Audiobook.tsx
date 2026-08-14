import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import Layout from "../components/Layout";

// ── Types ───────────────────────────────────────────────────────────────────

interface Chapter {
  id: number;
  source: string;
  title: string;
  script_status: string;
  cast_status: string;
  generation_status: string;
  segments: Record<string, unknown>[];
  script_path?: string;
}

interface AudiobookState {
  stage: string;
  chapters: Chapter[];
  casting: Record<string, { voice_key: string; approved: boolean }>;
  casting_config: Record<string, Record<string, unknown>>;
  qa_reports: Record<string, Record<string, unknown>>;
  regeneration_queue: { segment_id: string; notes: string; status: string }[];
}

// ── Main component ──────────────────────────────────────────────────────────

export default function Audiobook() {
  const { id: projectId } = useParams<{ id: string }>();
  const [state, setState] = useState<AudiobookState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadState = useCallback(async () => {
    if (!projectId) return;
    try {
      const data = await api.get(`/audiobook/${projectId}/state`);
      setState(data as AudiobookState);
    } catch (e) {
      // Not initialized yet — that's fine
      setState(null);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { loadState(); }, [loadState]);

  const initAudiobook = async () => {
    if (!projectId) return;
    setBusy(true); setError("");
    try {
      await api.post(`/audiobook/${projectId}/init`, {});
      await loadState();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  if (loading) return <Layout><div className="text-gray-500">Loading…</div></Layout>;

  if (!state) {
    return (
      <Layout>
        <div className="mx-auto max-w-2xl text-center py-20">
          <h1 className="text-2xl font-bold text-gray-100 mb-4">Audiobook Generator</h1>
          <p className="text-gray-400 mb-6">
            Convert your manuscript into a full-cast audiobook with automated QA.
          </p>
          <button className="btn-primary" onClick={initAudiobook} disabled={busy}>
            {busy ? "Initializing…" : "Initialize Audiobook Pipeline"}
          </button>
          {error && <p className="mt-4 text-sm text-red-400">{error}</p>}
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-100">Audiobook Generator</h1>
          <p className="text-sm text-gray-500">
            Stage: <span className="font-medium text-accent">{state.stage}</span>
          </p>
        </div>
        <Link to={`/project/${projectId}`} className="btn-ghost text-xs">
          ← Back to project
        </Link>
      </div>

      {/* Stage navigation */}
      <div className="mb-6 flex gap-2">
        {["script", "cast", "generate", "review"].map((s) => (
          <div key={s}
            className={`rounded px-3 py-1.5 text-xs font-medium ${
              state.stage === s ? "bg-accent text-white" : "bg-ink-800 text-gray-500"
            }`}>
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </div>
        ))}
      </div>

      {/* Stage content */}
      {state.stage === "script" && <ScriptStage projectId={projectId!} state={state} onChange={loadState} />}
      {state.stage === "cast" && <CastStage projectId={projectId!} state={state} onChange={loadState} />}
      {state.stage === "generate" && <GenerateStage projectId={projectId!} state={state} onChange={loadState} />}
      {state.stage === "review" && <ReviewStage projectId={projectId!} state={state} onChange={loadState} />}
      {state.stage === "complete" && (
        <div className="card p-6 text-center">
          <h2 className="text-lg font-semibold text-emerald-400 mb-2">Audiobook Complete</h2>
          <p className="text-gray-400">All chapters have been reviewed and approved.</p>
        </div>
      )}
    </Layout>
  );
}


// ── Script Stage ────────────────────────────────────────────────────────────

function ScriptStage({ projectId, state, onChange }: { projectId: string; state: AudiobookState; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editingChapter, setEditingChapter] = useState<number | null>(null);
  const [scriptContent, setScriptContent] = useState("");

  const [generatedCount, setGeneratedCount] = useState<number | null>(null);

  const generateScripts = async () => {
    setBusy(true); setError(""); setGeneratedCount(null);
    try {
      const res = await api.post(`/audiobook/${projectId}/script/generate`, {}) as { generated: number; errors?: string[] };
      await onChange();
      if (res.generated === 0) {
        setError("No scripts were generated. Check that you have manuscript chapters and a model configured in Settings → Model routing.");
      } else {
        setGeneratedCount(res.generated);
      }
      if (res.errors && res.errors.length > 0) {
        setError(res.errors.join("; "));
      }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const loadScript = async (chapterId: number) => {
    try {
      const data = await api.get(`/audiobook/${projectId}/script/${chapterId}`) as { content: string };
      setScriptContent(data.content || "");
      setEditingChapter(chapterId);
    } catch (e) { setError((e as Error).message); }
  };

  const saveScript = async () => {
    if (editingChapter === null) return;
    setBusy(true); setError("");
    try {
      await api.post(`/audiobook/${projectId}/script/edit`, {
        chapter_id: editingChapter,
        content: scriptContent,
      });
      setEditingChapter(null);
      await onChange();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const approveScript = async (chapterId: number) => {
    try {
      await api.post(`/audiobook/${projectId}/script/${chapterId}/approve`, {});
      await onChange();
    } catch (e) { setError((e as Error).message); }
  };

  const allApproved = state.chapters.every(ch => ch.script_status === "approved");

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h3 className="mb-3 text-base font-semibold text-gray-100">Script Generation</h3>
        <p className="text-sm text-gray-500 mb-4">
          Generate audio scripts from your manuscript. Each chapter is converted into
          segments with narrator directions, dialogue attribution, and voice assignments.
          You can edit scripts before approving.
        </p>
        <button className="btn-primary" onClick={generateScripts} disabled={busy}>
          {busy ? "Generating…" : "Generate Scripts for All Chapters"}
        </button>
      </div>

      {state.chapters.map((ch) => (
        <div key={ch.id} className="card p-4">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-medium text-gray-200">{ch.title}</h4>
            <div className="flex items-center gap-2">
              <span className={`badge ${
                ch.script_status === "approved" ? "bg-emerald-600/15 text-emerald-300" :
                ch.script_status === "draft" ? "bg-amber-600/15 text-amber-300" :
                "bg-ink-800 text-gray-500"
              }`}>{ch.script_status}</span>
              {ch.script_status === "draft" && (
                <>
                  <button className="btn-ghost !py-1 text-xs" onClick={() => loadScript(ch.id)}>Edit</button>
                  <button className="btn-ghost !py-1 text-xs" onClick={() => approveScript(ch.id)}>Approve</button>
                </>
              )}
            </div>
          </div>

          {editingChapter === ch.id && (
            <div className="mt-3 space-y-2">
              <textarea
                className="input h-64 resize-y font-mono text-xs"
                value={scriptContent}
                onChange={(e) => setScriptContent(e.target.value)}
              />
              <div className="flex gap-2">
                <button className="btn-primary !py-1 text-xs" onClick={saveScript} disabled={busy}>Save</button>
                <button className="btn-ghost !py-1 text-xs" onClick={() => setEditingChapter(null)}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      ))}

      {generatedCount !== null && generatedCount > 0 && (
        <p className="text-xs text-emerald-400">Generated scripts for {generatedCount} chapter(s).</p>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}

      {allApproved && (
        <button className="btn-primary w-full" onClick={async () => {
          await api.post(`/audiobook/${projectId}/advance-stage`, {});
          await onChange();
        }}>
          Advance to Casting →
        </button>
      )}
    </div>
  );
}


// ── Cast Stage ──────────────────────────────────────────────────────────────

function CastStage({ projectId, state, onChange }: { projectId: string; state: AudiobookState; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [suggestions, setSuggestions] = useState<Record<string, { voice_key: string; one_line: string; design_prompt: string }[]>>({});

  useEffect(() => {
    api.get(`/audiobook/${projectId}/cast/suggestions`).then((data) => {
      setSuggestions((data as { suggestions: Record<string, { voice_key: string; one_line: string; design_prompt: string }[]> }).suggestions);
    }).catch(() => {});
  }, [projectId]);

  const assignVoice = async (character: string, voiceKey: string) => {
    try {
      await api.post(`/audiobook/${projectId}/cast/assign`, { character, voice_key: voiceKey });
      await onChange();
    } catch (e) { setError((e as Error).message); }
  };

  const approveCast = async () => {
    setBusy(true); setError("");
    try {
      await api.post(`/audiobook/${projectId}/cast/approve`, { approved: true });
      await onChange();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h3 className="mb-3 text-base font-semibold text-gray-100">Voice Casting</h3>
        <p className="text-sm text-gray-500 mb-4">
          Assign voices to each character. Each main character has multiple voice options
          (A, B, C). Select the voice that best fits each character.
        </p>
      </div>

      {Object.entries(suggestions).map(([charName, options]) => {
        const current = state.casting[charName];
        return (
          <div key={charName} className="card p-4">
            <h4 className="text-sm font-medium text-gray-200 mb-2">{charName.replace(/_/g, " ")}</h4>
            <div className="grid gap-2 md:grid-cols-3">
              {options.map((opt) => (
                <button key={opt.voice_key}
                  className={`rounded border p-3 text-left text-xs ${
                    current?.voice_key === opt.voice_key
                      ? "border-accent bg-accent/10"
                      : "border-edge hover:border-gray-600"
                  }`}
                  onClick={() => assignVoice(charName, opt.voice_key)}>
                  <div className="font-medium text-gray-200">{opt.voice_key}</div>
                  <div className="mt-1 text-gray-400">{opt.one_line}</div>
                </button>
              ))}
            </div>
          </div>
        );
      })}

      {error && <p className="text-xs text-red-400">{error}</p>}

      <button className="btn-primary w-full" onClick={approveCast} disabled={busy}>
        {busy ? "Approving…" : "Approve Cast & Advance to Generation →"}
      </button>
    </div>
  );
}


// ── Generate Stage ──────────────────────────────────────────────────────────

function GenerateStage({ projectId, state, onChange }: { projectId: string; state: AudiobookState; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const generateAll = async () => {
    setBusy(true); setError("");
    try {
      await api.post(`/audiobook/${projectId}/generate`, { chapter_ids: [] });
      await onChange();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const advanceToReview = async () => {
    setBusy(true); setError("");
    try {
      await api.post(`/audiobook/${projectId}/advance-stage`, {});
      await onChange();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h3 className="mb-3 text-base font-semibold text-gray-100">Audio Generation</h3>
        <p className="text-sm text-gray-500 mb-4">
          Generate TTS audio for all chapters. QA runs automatically after each chapter.
        </p>
        <button className="btn-primary" onClick={generateAll} disabled={busy}>
          {busy ? "Generating…" : "Generate All Chapters"}
        </button>
      </div>

      {state.chapters.map((ch) => (
        <div key={ch.id} className="card p-4">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-gray-200">{ch.title}</h4>
            <span className={`badge ${
              ch.generation_status === "qa_pass" || ch.generation_status === "reviewed"
                ? "bg-emerald-600/15 text-emerald-300"
                : ch.generation_status === "generating"
                ? "bg-amber-600/15 text-amber-300"
                : ch.generation_status === "qa_fail"
                ? "bg-red-600/15 text-red-300"
                : "bg-ink-800 text-gray-500"
            }`}>{ch.generation_status}</span>
          </div>
          <p className="text-xs text-gray-500 mt-1">{ch.segments.length} segments</p>
        </div>
      ))}

      {error && <p className="text-xs text-red-400">{error}</p>}

      {state.chapters.every(ch => ch.generation_status !== "pending") && (
        <button className="btn-primary w-full" onClick={advanceToReview} disabled={busy}>
          Advance to Review →
        </button>
      )}
    </div>
  );
}


// ── Review Stage ────────────────────────────────────────────────────────────

function ReviewStage({ projectId, state, onChange }: { projectId: string; state: AudiobookState; onChange: () => void }) {
  const [error, setError] = useState("");
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [segments, setSegments] = useState<Record<string, unknown>[]>([]);

  const loadSegments = async (chapterId: number) => {
    try {
      const data = await api.get(`/audiobook/${projectId}/segments/${chapterId}`) as { segments: Record<string, unknown>[] };
      setSegments(data.segments);
      setSelectedChapter(chapterId);
    } catch (e) { setError((e as Error).message); }
  };

  const markForRegeneration = async (segmentId: string) => {
    try {
      await api.post(`/audiobook/${projectId}/review/mark`, {
        segment_id: segmentId,
        action: "regenerate",
        notes: "Marked for regeneration during review",
      });
      await onChange();
    } catch (e) { setError((e as Error).message); }
  };

  const approveChapter = async (chapterId: number) => {
    try {
      await api.post(`/audiobook/${projectId}/review/approve-chapter/${chapterId}`, {});
      await onChange();
    } catch (e) { setError((e as Error).message); }
  };

  const completeReview = async () => {
    try {
      await api.post(`/audiobook/${projectId}/review/complete`, {});
      await onChange();
    } catch (e) { setError((e as Error).message); }
  };

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h3 className="mb-3 text-base font-semibold text-gray-100">Review</h3>
        <p className="text-sm text-gray-500 mb-4">
          Listen to each chapter section by section. Mark segments that need regeneration.
        </p>
      </div>

      {/* Regeneration queue */}
      {state.regeneration_queue.length > 0 && (
        <div className="card border border-amber-600/30 p-4">
          <h4 className="text-sm font-semibold text-amber-300 mb-2">Regeneration Queue</h4>
          {state.regeneration_queue.map((r) => (
            <div key={r.segment_id} className="flex items-center justify-between text-xs text-gray-400 py-1">
              <span>{r.segment_id}</span>
              <span className="badge bg-amber-600/15 text-amber-300">{r.status}</span>
            </div>
          ))}
        </div>
      )}

      {/* Chapter list */}
      {state.chapters.map((ch) => (
        <div key={ch.id} className="card p-4">
          <div className="flex items-center justify-between mb-2">
            <button className="text-sm font-medium text-gray-200 hover:text-accent"
              onClick={() => loadSegments(ch.id)}>
              {ch.title}
            </button>
            <div className="flex items-center gap-2">
              <span className={`badge ${
                ch.generation_status === "reviewed" ? "bg-emerald-600/15 text-emerald-300" :
                ch.generation_status === "qa_fail" ? "bg-red-600/15 text-red-300" :
                "bg-ink-800 text-gray-500"
              }`}>{ch.generation_status}</span>
              {ch.generation_status !== "reviewed" && (
                <button className="btn-ghost !py-1 text-xs" onClick={() => approveChapter(ch.id)}>
                  Approve
                </button>
              )}
            </div>
          </div>

          {/* Segments for selected chapter */}
          {selectedChapter === ch.id && segments.length > 0 && (
            <div className="mt-3 space-y-2">
              {segments.map((seg, i) => (
                <div key={i} className="flex items-center justify-between rounded border border-edge/50 p-2 text-xs">
                  <div className="flex-1">
                    <span className="font-medium text-gray-300">{String(seg.segment_id)}</span>
                    <span className="ml-2 text-gray-500">{String(seg.kind)}</span>
                    <span className="ml-2 text-gray-500">→ {String(seg.voice_id)}</span>
                    <p className="mt-1 text-gray-400 truncate">{String(seg.source_text).substring(0, 100)}…</p>
                  </div>
                  <button className="btn-ghost !py-1 text-xs text-red-400"
                    onClick={() => markForRegeneration(String(seg.segment_id))}>
                    Regenerate
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {error && <p className="text-xs text-red-400">{error}</p>}

      {state.chapters.every(ch => ch.generation_status === "reviewed") && (
        <button className="btn-primary w-full" onClick={completeReview}>
          Complete Audiobook
        </button>
      )}
    </div>
  );
}
