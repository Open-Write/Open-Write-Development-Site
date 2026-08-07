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

export default function VersionHistory({ projectId }: { projectId: string }) {
  const [groups, setGroups] = useState<VersionGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

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
    setDetailLoading(true);
    setDetail(null);
    api
      .versionDetail(v.id)
      .then(setDetail)
      .catch((e) => setError(e.message))
      .finally(() => setDetailLoading(false));
  };

  if (loading) return <div className="p-6 text-gray-500">Loading version history…</div>;

  return (
    <div className="flex gap-6">
      {/* List column */}
      <div className="w-full max-w-md shrink-0 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-300">
            Version History <span className="text-gray-500">({total})</span>
          </h3>
          <button className="btn-ghost !py-1 !px-2 text-xs" onClick={load}>Refresh</button>
        </div>

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
                          detail?.id === v.id ? "bg-ink-800 ring-1 ring-accent-soft/50" : "",
                        ].join(" ")}
                      >
                        <span className="flex items-center gap-2">
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

      {/* Detail column */}
      <div className="min-w-0 flex-1">
        <div className="card sticky top-4 max-h-[calc(100vh-6rem)] overflow-hidden">
          {!detail && !detailLoading && (
            <div className="p-8 text-center text-sm text-gray-500">
              Select a version to view its captured content.
            </div>
          )}
          {detailLoading && <div className="p-8 text-center text-gray-500">Loading…</div>}
          {detail && (
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
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
