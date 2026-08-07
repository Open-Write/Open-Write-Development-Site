import { useEffect, useState, useCallback } from "react";
import { api } from "../api";
import Layout from "../components/Layout";

// ── Types ──────────────────────────────────────────────────────────────────

interface ReviewSummary {
  id: string; title: string; format: string; created_at: string; updated_at: string;
}
interface ReviewDetail {
  id: string; title: string; format: string;
  original_content: string; current_content: string;
  supporting_materials: Record<string, string>;
  reports: { report_type: string; report: string; verdict: string; created_at: string }[];
  versions: { version_number: number; feedback: string; instructions: string; created_at: string }[];
  created_at: string; updated_at: string;
}
interface CriticOption {
  id: string; label: string; description: string; category: string;
}
interface VersionDetail {
  version_number: number; content: string; feedback: string; instructions: string; created_at: string;
}

const VERDICT_COLORS: Record<string, string> = {
  PASS: "bg-emerald-600/15 text-emerald-300",
  ADVANCE: "bg-blue-600/15 text-blue-300",
  REVISE: "bg-red-600/15 text-red-300",
  RECOMMEND: "bg-emerald-600/15 text-emerald-300",
  CONSIDER: "bg-amber-600/15 text-amber-300",
  REJECTION: "bg-red-600/15 text-red-300",
  "READ WITH EDITORIAL": "bg-amber-600/15 text-amber-300",
  "ACQUISITION RECOMMENDATION": "bg-emerald-600/15 text-emerald-300",
  ENGAGED: "bg-emerald-600/15 text-emerald-300",
  "WOULD CONTINUE": "bg-blue-600/15 text-blue-300",
  "READING WITH RESERVATIONS": "bg-amber-600/15 text-amber-300",
  "WOULD STOP": "bg-red-600/15 text-red-300",
  "WOULD SET DOWN": "bg-red-600/15 text-red-300",
  ERROR: "bg-red-600/15 text-red-300",
  UNKNOWN: "bg-ink-800 text-gray-400",
};

// ── Main component ─────────────────────────────────────────────────────────

export default function EditorialReview() {
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [critics, setCritics] = useState<CriticOption[]>([]);
  const [activeReview, setActiveReview] = useState<ReviewDetail | null>(null);
  const [view, setView] = useState<"list" | "create" | "detail">("list");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Create form state
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");
  const [newFormat, setNewFormat] = useState("prose");

  // Detail view state
  const [activeTab, setActiveTab] = useState<"content" | "reports" | "versions" | "materials">("content");
  const [selectedCritics, setSelectedCritics] = useState<Set<string>>(new Set());
  const [selectedReaders, setSelectedReaders] = useState<Set<string>>(new Set());
  const [versions, setVersions] = useState<VersionDetail[]>([]);
  const [viewingVersion, setViewingVersion] = useState<number | null>(null);
  const [revisionRounds, setRevisionRounds] = useState(1);
  const [revisionInstructions, setRevisionInstructions] = useState("");
  const [materialsBusy, setMaterialsBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [readerBusy, setReaderBusy] = useState(false);
  const [reviseBusy, setReviseBusy] = useState(false);
  const [activeReport, setActiveReport] = useState<string | null>(null);

  // ── Load data ────────────────────────────────────────────────────────────

  const loadReviews = useCallback(async () => {
    try {
      const res = await api.listEditorialReviews();
      setReviews(res.reviews);
    } catch (e) { setError((e as Error).message); }
  }, []);

  const loadCritics = useCallback(async () => {
    try {
      const res = await api.listCritics();
      setCritics(res.critics);
      setSelectedCritics(new Set(res.critics.filter((c) => c.category === "critic").map((c) => c.id)));
      setSelectedReaders(new Set());
    } catch (e) { /* ignore */ }
  }, []);

  useEffect(() => { loadReviews(); loadCritics(); }, [loadReviews, loadCritics]);

  const loadReview = async (id: string) => {
    setBusy(true);
    try {
      const res = await api.getEditorialReview(id);
      setActiveReview(res);
      setView("detail");
      setActiveTab("content");
      setActiveReport(null);
      setViewingVersion(null);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const loadVersions = async (id: string) => {
    try {
      const res = await api.getEditorialVersions(id);
      setVersions(res.versions);
    } catch (e) { /* ignore */ }
  };

  // ── Actions ──────────────────────────────────────────────────────────────

  const createReview = async () => {
    if (!newContent.trim()) return;
    setBusy(true); setError("");
    try {
      const res = await api.createEditorialReview({
        title: newTitle.trim() || "Untitled",
        content: newContent.trim(),
        format: newFormat,
      });
      setNewTitle(""); setNewContent("");
      await loadReview(res.id);
      await loadReviews();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const runCritics = async () => {
    if (!activeReview) return;
    setReviewBusy(true); setError("");
    try {
      await api.runEditorialCritics(activeReview.id, {
        critics: Array.from(selectedCritics),
      });
      await loadReview(activeReview.id);
    } catch (e) { setError((e as Error).message); }
    finally { setReviewBusy(false); }
  };

  const runReader = async (readerType: string) => {
    if (!activeReview) return;
    setReaderBusy(true); setError("");
    try {
      await api.runEditorialReader(activeReview.id, { reader_type: readerType });
      await loadReview(activeReview.id);
      setActiveTab("reports");
    } catch (e) { setError((e as Error).message); }
    finally { setReaderBusy(false); }
  };

  const runRevision = async () => {
    if (!activeReview) return;
    setReviseBusy(true); setError("");
    try {
      await api.editorialRevise(activeReview.id, {
        instructions: revisionInstructions.trim(),
        rounds: revisionRounds,
      });
      await loadReview(activeReview.id);
      await loadVersions(activeReview.id);
    } catch (e) { setError((e as Error).message); }
    finally { setReviseBusy(false); }
  };

  const generateMaterials = async () => {
    if (!activeReview) return;
    setMaterialsBusy(true); setError("");
    try {
      await api.generateEditorialMaterials(activeReview.id, {});
      await loadReview(activeReview.id);
      setActiveTab("materials");
    } catch (e) { setError((e as Error).message); }
    finally { setMaterialsBusy(false); }
  };

  const deleteReview = async (id: string) => {
    try {
      await api.deleteEditorialReview(id);
      if (activeReview?.id === id) { setActiveReview(null); setView("list"); }
      await loadReviews();
    } catch (e) { setError((e as Error).message); }
  };

  const downloadContent = (text: string, filename: string) => {
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <Layout>
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-100">Editorial Review</h1>
            <p className="mt-1 text-sm text-gray-500">
              Upload work, run critics and adversarial readers, revise with version tracking.
            </p>
          </div>
          {view === "detail" && (
            <button className="btn-ghost" onClick={() => { setView("list"); setActiveReview(null); }}>
              ← Back to reviews
            </button>
          )}
          {view === "list" && (
            <button className="btn-primary" onClick={() => setView("create")}>
              + New review
            </button>
          )}
        </div>

        {error && (
          <div className="rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}
            <button className="ml-2 underline" onClick={() => setError("")}>dismiss</button>
          </div>
        )}

        {/* ── List view ─────────────────────────────────────────────────── */}
        {view === "list" && (
          <div className="space-y-3">
            {reviews.length === 0 && (
              <div className="card p-8 text-center text-gray-500">
                No reviews yet. Click "+ New review" to upload work for editorial review.
              </div>
            )}
            {reviews.map((r) => (
              <div key={r.id} className="card flex items-center justify-between p-4">
                <button className="text-left" onClick={() => loadReview(r.id)}>
                  <h3 className="font-medium text-gray-200">{r.title}</h3>
                  <p className="text-xs text-gray-500">
                    {r.format} · Updated {new Date(r.updated_at).toLocaleDateString()}
                  </p>
                </button>
                <button
                  className="btn-ghost !py-1 text-xs text-red-400"
                  onClick={() => deleteReview(r.id)}
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}

        {/* ── Create view ───────────────────────────────────────────────── */}
        {view === "create" && (
          <div className="card space-y-4 p-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs text-gray-400">Title</label>
                <input className="input" placeholder="My screenplay draft 1"
                  value={newTitle} onChange={(e) => setNewTitle(e.target.value)} />
              </div>
              <div>
                <label className="mb-1 block text-xs text-gray-400">Format</label>
                <select className="input" value={newFormat}
                  onChange={(e) => setNewFormat(e.target.value)}>
                  <option value="prose">Prose / Literary Fiction</option>
                  <option value="screenplay">Screenplay</option>
                  <option value="tv">TV / Teleplay</option>
                </select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">Paste your text</label>
              <textarea className="input h-64 resize-y font-serif"
                placeholder="Paste your chapter, scene, essay, screenplay, or any text here…"
                value={newContent} onChange={(e) => setNewContent(e.target.value)} />
              <p className="mt-1 text-xs text-gray-500">
                {newContent.trim().split(/\s+/).filter(Boolean).length} words
              </p>
            </div>
            <div className="flex gap-3">
              <button className="btn-primary" onClick={createReview}
                disabled={busy || !newContent.trim()}>
                {busy ? "Saving…" : "Save & start review"}
              </button>
              <button className="btn-ghost" onClick={() => setView("list")}>Cancel</button>
            </div>
          </div>
        )}

        {/* ── Detail view ───────────────────────────────────────────────── */}
        {view === "detail" && activeReview && (
          <DetailTabs
            review={activeReview}
            critics={critics}
            selectedCritics={selectedCritics}
            setSelectedCritics={setSelectedCritics}
            selectedReaders={selectedReaders}
            setSelectedReaders={setSelectedReaders}
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            activeReport={activeReport}
            setActiveReport={setActiveReport}
            versions={versions}
            viewingVersion={viewingVersion}
            setViewingVersion={setViewingVersion}
            loadVersions={loadVersions}
            revisionRounds={revisionRounds}
            setRevisionRounds={setRevisionRounds}
            revisionInstructions={revisionInstructions}
            setRevisionInstructions={setRevisionInstructions}
            runCritics={runCritics}
            runReader={runReader}
            runRevision={runRevision}
            generateMaterials={generateMaterials}
            downloadContent={downloadContent}
            reviewBusy={reviewBusy}
            readerBusy={readerBusy}
            reviseBusy={reviseBusy}
            materialsBusy={materialsBusy}
          />
        )}
      </div>
    </Layout>
  );
}

// ── Detail tabs component ──────────────────────────────────────────────────

function DetailTabs(props: {
  review: ReviewDetail;
  critics: CriticOption[];
  selectedCritics: Set<string>; setSelectedCritics: (s: Set<string>) => void;
  selectedReaders: Set<string>; setSelectedReaders: (s: Set<string>) => void;
  activeTab: string; setActiveTab: (t: "content" | "reports" | "versions" | "materials") => void;
  activeReport: string | null; setActiveReport: (r: string | null) => void;
  versions: VersionDetail[]; viewingVersion: number | null;
  setViewingVersion: (v: number | null) => void; loadVersions: (id: string) => void;
  revisionRounds: number; setRevisionRounds: (n: number) => void;
  revisionInstructions: string; setRevisionInstructions: (s: string) => void;
  runCritics: () => void; runReader: (t: string) => void;
  runRevision: () => void; generateMaterials: () => void;
  downloadContent: (t: string, f: string) => void;
  reviewBusy: boolean; readerBusy: boolean; reviseBusy: boolean; materialsBusy: boolean;
}) {
  const { review, critics, activeTab, setActiveTab, activeReport, setActiveReport } = props;
  const criticOptions = critics.filter((c) => c.category === "critic");
  const readerOptions = critics.filter((c) => c.category === "reader");

  const toggleCritic = (id: string) => {
    const next = new Set(props.selectedCritics);
    if (next.has(id)) next.delete(id); else next.add(id);
    props.setSelectedCritics(next);
  };

  const tabs = [
    { key: "content", label: "Content" },
    { key: "reports", label: `Reports (${review.reports.length})` },
    { key: "versions", label: `Versions (${review.versions.length})` },
    { key: "materials", label: "Materials" },
  ] as const;

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex gap-1 border-b border-edge">
        {tabs.map((t) => (
          <button key={t.key}
            className={`px-4 py-2 text-sm transition-colors ${
              activeTab === t.key
                ? "border-b-2 border-accent text-gray-100"
                : "text-gray-500 hover:text-gray-300"
            }`}
            onClick={() => setActiveTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Content tab */}
      {activeTab === "content" && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <div className="card p-5">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-gray-100">{review.title}</h2>
                <span className="badge bg-ink-800 text-gray-400">{review.format}</span>
              </div>
              <div className="max-h-[60rem] overflow-y-auto whitespace-pre-wrap font-serif text-sm text-gray-300 leading-relaxed">
                {props.viewingVersion !== null
                  ? (props.versions.find((v) => v.version_number === props.viewingVersion)?.content || "Loading…")
                  : review.current_content}
              </div>
              {props.viewingVersion !== null && (
                <button className="btn-ghost mt-3 text-xs"
                  onClick={() => props.setViewingVersion(null)}>
                  ← Back to current version
                </button>
              )}
            </div>
          </div>

          {/* Sidebar: critics + readers + revision */}
          <div className="space-y-4">
            {/* Critics */}
            <div className="card p-4">
              <h3 className="mb-2 text-sm font-semibold text-gray-300">Critics</h3>
              <div className="space-y-1">
                {criticOptions.map((c) => (
                  <label key={c.id} className="flex items-center gap-2 text-xs text-gray-400">
                    <input type="checkbox" checked={props.selectedCritics.has(c.id)}
                      onChange={() => toggleCritic(c.id)} className="rounded border-edge" />
                    {c.label}
                  </label>
                ))}
              </div>
              <button className="btn-primary mt-3 w-full text-xs"
                onClick={props.runCritics}
                disabled={props.reviewBusy || props.selectedCritics.size === 0}>
                {props.reviewBusy ? "Running…" : `Run ${props.selectedCritics.size} critic${props.selectedCritics.size === 1 ? "" : "s"}`}
              </button>
            </div>

            {/* Readers */}
            <div className="card p-4">
              <h3 className="mb-2 text-sm font-semibold text-gray-300">Adversarial Readers</h3>
              <div className="space-y-2">
                {readerOptions.map((r) => (
                  <button key={r.id}
                    className="btn-ghost w-full text-left text-xs"
                    onClick={() => props.runReader(r.id)}
                    disabled={props.readerBusy}>
                    {props.readerBusy ? "Running…" : r.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Revision */}
            <div className="card p-4">
              <h3 className="mb-2 text-sm font-semibold text-gray-300">Revision</h3>
              <div className="space-y-2">
                <div>
                  <label className="text-xs text-gray-400">Rounds</label>
                  <input type="number" className="input w-20" min={1} max={10}
                    value={props.revisionRounds}
                    onChange={(e) => props.setRevisionRounds(Math.max(1, Math.min(10, Number(e.target.value) || 1)))} />
                </div>
                <input className="input text-xs" placeholder="Optional instructions…"
                  value={props.revisionInstructions}
                  onChange={(e) => props.setRevisionInstructions(e.target.value)} />
                <button className="btn-primary w-full text-xs"
                  onClick={props.runRevision} disabled={props.reviseBusy}>
                  {props.reviseBusy ? "Revising…" : `Revise (${props.revisionRounds} round${props.revisionRounds === 1 ? "" : "s"})`}
                </button>
              </div>
            </div>

            {/* Materials */}
            <div className="card p-4">
              <h3 className="mb-2 text-sm font-semibold text-gray-300">Supporting Materials</h3>
              <button className="btn-ghost w-full text-xs"
                onClick={props.generateMaterials} disabled={props.materialsBusy}>
                {props.materialsBusy ? "Generating…" : "Generate bible, profiles, format rules"}
              </button>
            </div>

            {/* Download */}
            <div className="card p-4">
              <button className="btn-ghost w-full text-xs"
                onClick={() => props.downloadContent(review.current_content, `${review.title}.txt`)}>
                ↓ Download current version
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reports tab */}
      {activeTab === "reports" && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-1">
            {review.reports.map((r, i) => (
              <button key={i}
                onClick={() => setActiveReport(r.report_type)}
                className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                  activeReport === r.report_type ? "bg-ink-800 text-gray-100" : "text-gray-400 hover:bg-ink-850"
                }`}>
                <span className="capitalize">{r.report_type.replace(/_/g, " ")}</span>
                <span className={`badge text-xs ${VERDICT_COLORS[r.verdict] || VERDICT_COLORS.UNKNOWN}`}>
                  {r.verdict}
                </span>
              </button>
            ))}
            {review.reports.length === 0 && (
              <p className="p-3 text-xs text-gray-500">No reports yet. Run critics or readers from the Content tab.</p>
            )}
          </div>
          <div className="lg:col-span-2">
            <div className="card min-h-[30rem] p-5">
              {activeReport && review.reports.find((r) => r.report_type === activeReport) ? (
                <div>
                  <h2 className="mb-3 text-lg font-semibold text-gray-100 capitalize">
                    {activeReport.replace(/_/g, " ")}
                  </h2>
                  <div className="prose-sm max-w-none whitespace-pre-wrap font-serif text-gray-300 leading-relaxed">
                    {review.reports.find((r) => r.report_type === activeReport)?.report}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-gray-500">Select a report to view.</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Versions tab */}
      {activeTab === "versions" && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-1">
            {props.versions.length === 0 && review.versions.length > 0 && (
              <button className="btn-ghost w-full text-xs"
                onClick={() => props.loadVersions(review.id)}>
                Load version history
              </button>
            )}
            {(props.versions.length > 0 ? props.versions : review.versions).map((v) => (
              <button key={v.version_number}
                onClick={() => { props.setViewingVersion(v.version_number); props.loadVersions(review.id); }}
                className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                  props.viewingVersion === v.version_number ? "bg-ink-800 text-gray-100" : "text-gray-400 hover:bg-ink-850"
                }`}>
                <span>Version {v.version_number}</span>
                <span className="text-xs text-gray-500">
                  {v.created_at ? new Date(v.created_at).toLocaleDateString() : ""}
                </span>
              </button>
            ))}
            {review.versions.length === 0 && (
              <p className="p-3 text-xs text-gray-500">Only the original upload. Revise to create versions.</p>
            )}
          </div>
          <div className="lg:col-span-2">
            <div className="card min-h-[30rem] p-5">
              {props.viewingVersion !== null && props.versions.length > 0 ? (
                <div>
                  <h2 className="mb-2 text-lg font-semibold text-gray-100">
                    Version {props.viewingVersion}
                  </h2>
                  {props.versions.find((v) => v.version_number === props.viewingVersion)?.instructions && (
                    <p className="mb-3 text-xs text-gray-500">
                      Instructions: {props.versions.find((v) => v.version_number === props.viewingVersion)?.instructions}
                    </p>
                  )}
                  <div className="max-h-[50rem] overflow-y-auto whitespace-pre-wrap font-serif text-sm text-gray-300 leading-relaxed">
                    {props.versions.find((v) => v.version_number === props.viewingVersion)?.content}
                  </div>
                </div>
              ) : (
                <div>
                  <h2 className="mb-2 text-lg font-semibold text-gray-100">Original upload</h2>
                  <div className="max-h-[50rem] overflow-y-auto whitespace-pre-wrap font-serif text-sm text-gray-300 leading-relaxed">
                    {review.original_content}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Materials tab */}
      {activeTab === "materials" && (
        <div className="space-y-4">
          {Object.keys(review.supporting_materials).length === 0 ? (
            <div className="card p-8 text-center text-gray-500">
              No supporting materials generated yet. Click "Generate" from the Content tab.
            </div>
          ) : (
            Object.entries(review.supporting_materials).map(([key, value]) => (
              <div key={key} className="card p-5">
                <h3 className="mb-3 text-sm font-semibold text-gray-300 capitalize">{key.replace(/_/g, " ")}</h3>
                <div className="max-h-96 overflow-y-auto whitespace-pre-wrap font-serif text-sm text-gray-300 leading-relaxed">
                  {value}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
