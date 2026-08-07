import { useEffect, useState } from "react";
import { api, type VersionGroup, type VersionSummary, type VersionDetail } from "../api";
import MarkdownViewer from "./MarkdownViewer";

// Human labels for content_type codes captured by the backend.
const CT_LABELS: Record<string, string> = {
  bible: "Story Bible",
  voice: "Voice Profile",
  editorial: "Editorial Plan",
  chapter_draft: "Chapter Draft",
  chapter_final: "Chapter (Final)",
  user_edit: "Manual Edit",
  critic_report: "Critic Report",
  coverage_report: "Coverage Report",
  finalize: "Finalize Certificate",
};

function ctLabel(ct: string) {
  return CT_LABELS[ct] || ct.replace(/_/g, " ");
}

function verdictBadge(v: string | null) {
  if (!v) return null;
  const pass = /pass|approve|ok|accept/i.test(v);
  const fail = /fail|revis|reject|block/i.test(v);
  const cls = pass
    ? "bg-emerald-600/15 text-emerald-300"
    : fail
    ? "bg-red-600/15 text-red-300"
    : "bg-ink-800 text-gray-400";
  return <span className={`badge ${cls}`}>{v}</span>;
}

type DiffLine = { type: string; old_line?: string; new_line?: string };

export default function VersionHistory({ projectId }: { projectId: string }) {
  const [groups, setGroups] = useState<VersionGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // ── Restore state ──────────────────────────────────────────────────────
  const [restoring, setRestoring] = useState(false);
  const [restoreMsg, setRestoreMsg] = useState("");

  // ── Diff state ─────────────────────────────────────────────────────────
  const [diffMode, setDiffMode] = useState(false);
  const [diffSelected, setDiffSelected] = useState<string[]>([]); // up to 2 version ids
  const [diffResult, setDiffResult] = useState<{
    lines: DiffLine[];
    stats: { insertions: number; deletions: number; unchanged: number };
    content_type: string;
  } | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .listVersions(projectId)
      .then((r) => { setGroups(r.groups); setTotal(r.total); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [projectId]);

  const openDetail = (v: VersionSummary) => {
    // In diff mode, toggle selection instead of opening detail.
    if (diffMode) {
      toggleDiffSelect(v.id);
      return;
    }
    setDetailLoading(true);
    setDetail(null);
    setRestoreMsg("");
    api
      .versionDetail(v.id)
      .then(setDetail)
      .catch((e) => setError(e.message))
      .finally(() => setDetailLoading(false));
  };

  const toggleDiffSelect = (id: string) => {
    setDiffSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id]; // replace oldest
      return [...prev, id];
    });
  };

  const runDiff = async () => {
    if (diffSelected.length !== 2) return;
    setDiffLoading(true);
    setDiffResult(null);
    try {
      const r = await api.versionDiff(diffSelected[0], diffSelected[1]);
      setDiffResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDiffLoading(false);
    }
  };

  const doRestore = async () => {
    if (!detail) return;
    if (!confirm(`Restore version to "${ctLabel(detail.content_type)}"? This will overwrite the current file and capture a new version.`)) return;
    setRestoring(true);
    setRestoreMsg("");
    try {
      const r = await api.restoreVersion(projectId, detail.id);
      setRestoreMsg(`Restored to ${r.path} · ${r.word_count.toLocaleString()} words`);
      load(); // refresh version list
    } catch (e) {
      setRestoreMsg(`Error: ${(e as Error).message}`);
    } finally {
      setRestoring(false);
    }
  };

  // Collect all version ids across groups for the diff picker.
  const allVersions: VersionSummary[] = [];
  groups.forEach((g) => g.items.forEach((it) => allVersions.push(...it.versions)));

  if (loading) return <div className="p-6 text-gray-500">Loading version history…</div>;

  return (
    <div className="flex gap-6">
      {/* List column */}
      <div className="w-full max-w-md shrink-0 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-300">
            Version History <span className="text-gray-500">({total})</span>
          </h3>
          <div className="flex items-center gap-2">
            <button
              className={`btn-ghost !py-1 !px-2 text-xs ${diffMode ? "!bg-accent-soft/20 !text-accent" : ""}`}
              onClick={() => { setDiffMode(!diffMode); setDiffSelected([]); setDiffResult(null); }}
            >
              {diffMode ? "✕ Exit Diff" : "⇄ Diff"}
            </button>
            <button className="btn-ghost !py-1 !px-2 text-xs" onClick={load}>Refresh</button>
          </div>
        </div>

        {/* Diff controls */}
        {diffMode && (
          <div className="card p-3 space-y-2">
            <p className="text-xs text-gray-400">
              Select two versions to compare ({diffSelected.length}/2 selected)
            </p>
            {diffSelected.length === 2 && (
              <button
                className="btn-primary !py-1 w-full text-xs"
                onClick={runDiff}
                disabled={diffLoading}
              >
                {diffLoading ? "Computing…" : "Compare selected versions"}
              </button>
            )}
          </div>
        )}

        {error && <div className="rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}

        {total === 0 && (
          <div className="card p-6 text-center text-sm text-gray-500">
            No versions captured yet. Run the pipeline or save a chapter to build history.
          </div>
        )}

        {groups.map((g) => (
          <div key={g.group} className="card overflow-hidden">
            <div className="border-b border-edge bg-ink-850 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
              {g.group}
            </div>
            <div className="divide-y divide-edge/60">
              {g.items.map((item) => (
                <div key={item.content_type} className="px-4 py-3">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-200">{ctLabel(item.content_type)}</span>
                    <span className="text-xs text-gray-500">{item.versions.length} version{item.versions.length !== 1 ? "s" : ""}</span>
                  </div>
                  <div className="space-y-1">
                    {item.versions.map((v, idx) => (
                      <button
                        key={v.id}
                        onClick={() => openDetail(v)}
                        className={[
                          "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-ink-800",
                          detail?.id === v.id && !diffMode ? "bg-ink-800 ring-1 ring-accent-soft/50" : "",
                          diffSelected.includes(v.id) ? "bg-accent-soft/15 ring-1 ring-accent-soft/60" : "",
                        ].join(" ")}
                      >
                        <span className="flex items-center gap-2">
                          {diffMode && (
                            <span className={[
                              "inline-flex h-4 w-4 items-center justify-center rounded border text-[10px]",
                              diffSelected.includes(v.id)
                                ? "border-accent bg-accent text-white"
                                : "border-gray-600 text-gray-600",
                            ].join(" ")}>
                              {diffSelected.includes(v.id) ? "✓" : ""}
                            </span>
                          )}
                          <span className="text-gray-300">v{item.versions.length - idx}</span>
                          <span className="text-gray-500">{v.phase}</span>
                          {verdictBadge(v.critic_verdict)}
                        </span>
                        <span className="text-gray-500">
                          {v.word_count ? `${v.word_count.toLocaleString()} w` : ""}
                          {v.created_at ? ` · ${new Date(v.created_at).toLocaleString()}` : ""}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Detail / Diff column */}
      <div className="min-w-0 flex-1">
        <div className="card sticky top-4 max-h-[calc(100vh-6rem)] overflow-hidden">
          {/* Diff view */}
          {diffMode && diffResult && (
            <div className="flex h-full flex-col">
              <div className="flex items-center gap-3 border-b border-edge bg-ink-850 px-4 py-3">
                <span className="text-sm font-semibold text-gray-200">Diff: {ctLabel(diffResult.content_type)}</span>
                <span className="badge bg-emerald-600/15 text-emerald-300">+{diffResult.stats.insertions}</span>
                <span className="badge bg-red-600/15 text-red-300">−{diffResult.stats.deletions}</span>
                <span className="badge bg-ink-800 text-gray-400">{diffResult.stats.unchanged} unchanged</span>
              </div>
              <div className="flex-1 overflow-y-auto p-4">
                <pre className="font-mono text-xs leading-relaxed">
                  {diffResult.lines.map((line, i) => {
                    let cls = "text-gray-400";
                    let prefix = " ";
                    if (line.type === "insert") { cls = "text-emerald-300 bg-emerald-900/20"; prefix = "+"; }
                    else if (line.type === "delete") { cls = "text-red-300 bg-red-900/20"; prefix = "−"; }
                    else if (line.type === "replace") { cls = "text-amber-300 bg-amber-900/10"; prefix = "~"; }
                    const text = line.type === "delete" ? line.old_line : line.new_line;
                    return (
                      <div key={i} className={cls}>
                        <span className="select-none text-gray-600 mr-2">{prefix}</span>
                        {text}
                      </div>
                    );
                  })}
                </pre>
              </div>
            </div>
          )}

          {diffMode && !diffResult && !diffLoading && (
            <div className="p-8 text-center text-sm text-gray-500">
              Select two versions from the list and click "Compare" to see a diff.
            </div>
          )}
          {diffLoading && <div className="p-8 text-center text-gray-500">Computing diff…</div>}

          {/* Detail view */}
          {!diffMode && !detail && !detailLoading && (
            <div className="p-8 text-center text-sm text-gray-500">
              Select a version to view its captured content.
            </div>
          )}
          {!diffMode && detailLoading && <div className="p-8 text-center text-gray-500">Loading…</div>}
          {!diffMode && detail && (
            <div className="flex h-full flex-col">
              <div className="flex flex-wrap items-center gap-2 border-b border-edge bg-ink-850 px-4 py-3">
                <span className="text-sm font-semibold text-gray-200">{ctLabel(detail.content_type)}</span>
                <span className="badge bg-ink-800 text-gray-400">{detail.phase}</span>
                {detail.chapter_number != null && (
                  <span className="badge bg-ink-800 text-gray-400">Ch {detail.chapter_number}</span>
                )}
                {verdictBadge(detail.critic_verdict)}
                <span className="ml-auto text-xs text-gray-500">
                  {detail.word_count ? `${detail.word_count.toLocaleString()} words · ` : ""}
                  {detail.created_at ? new Date(detail.created_at).toLocaleString() : ""}
                </span>
              </div>
              <div className="overflow-y-auto p-5">
                <MarkdownViewer content={detail.content} />
              </div>
              {/* Restore bar */}
              <div className="flex shrink-0 items-center gap-3 border-t border-edge bg-ink-850 px-4 py-3">
                <button
                  className="btn-primary !py-1.5 text-xs"
                  onClick={doRestore}
                  disabled={restoring || !detail.content}
                >
                  {restoring ? "Restoring…" : "↩ Restore this version"}
                </button>
                {restoreMsg && (
                  <span className={`text-xs ${restoreMsg.startsWith("Error") ? "text-red-300" : "text-emerald-400"}`}>
                    {restoreMsg}
                  </span>
                )}
                <span className="ml-auto text-[10px] text-gray-500">
                  Restoring overwrites the current file and captures a new version.
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
