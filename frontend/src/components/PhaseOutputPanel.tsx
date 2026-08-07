import { useEffect, useState } from "react";
import { api } from "../api";
import MarkdownViewer from "./MarkdownViewer";

interface Entry {
  path: string; label?: string; group?: string; exists: boolean;
  words?: number | null; mtime?: string;
}
interface Category {
  key: string; label: string; count: number; exists_count: number; entries: Entry[];
}

// Browsable catalog of every artifact the pipeline has written to disk, grouped
// by category. Clicking an existing file loads its content on the right.
export default function PhaseOutputPanel({ projectId }: { projectId: string }) {
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Entry | null>(null);
  const [content, setContent] = useState("");
  const [fileLoading, setFileLoading] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .outputs(projectId)
      .then((r) => { setCategories((r as { categories: Category[] }).categories || []); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [projectId]);

  const open = (e: Entry) => {
    if (!e.exists) return;
    setSelected(e);
    setFileLoading(true);
    setContent("");
    api
      .outputFile(projectId, e.path)
      .then((r) => setContent(r.content || ""))
      .catch((err) => setError(err.message))
      .finally(() => setFileLoading(false));
  };

  if (loading) return <div className="p-6 text-gray-500">Loading outputs…</div>;

  return (
    <div className="flex gap-6">
      <div className="w-full max-w-sm shrink-0 space-y-4 sticky top-4 max-h-[calc(100vh-6rem)] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-300">Artifacts</h3>
          <button className="btn-ghost !py-1 !px-2 text-xs" onClick={load}>Refresh</button>
        </div>
        {error && <div className="rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}
        {categories.map((c) => (
          <div key={c.key} className="card overflow-hidden">
            <div className="flex items-center justify-between border-b border-edge bg-ink-850 px-4 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">{c.label}</span>
              <span className="text-xs text-gray-500">{c.exists_count}/{c.count}</span>
            </div>
            <div className="divide-y divide-edge/60">
              {c.entries.map((e) => (
                <button
                  key={e.path}
                  onClick={() => open(e)}
                  disabled={!e.exists}
                  className={[
                    "flex w-full items-center justify-between px-4 py-2 text-left text-xs transition-colors",
                    e.exists ? "hover:bg-ink-800 text-gray-300" : "cursor-default text-gray-600",
                    selected?.path === e.path ? "bg-ink-800 ring-1 ring-inset ring-accent-soft/40" : "",
                  ].join(" ")}
                >
                  <span className="truncate">{e.label || e.path.split("/").pop()}</span>
                  <span className="ml-2 shrink-0 text-gray-500">
                    {e.exists ? (e.words ? `${e.words.toLocaleString()} w` : "✓") : "—"}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="min-w-0 flex-1">
        <div className="card sticky top-4 h-[calc(100vh-6rem)] flex flex-col overflow-hidden">
          {!selected && <div className="p-8 text-center text-sm text-gray-500">Select an artifact to view.</div>}
          {selected && (
            <>
              <div className="flex shrink-0 items-center gap-2 border-b border-edge bg-ink-850 px-4 py-3">
                <span className="text-sm font-medium text-gray-200">{selected.label || selected.path}</span>
                <span className="ml-auto text-xs text-gray-500">{selected.path}</span>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto p-5">
                {fileLoading ? (
                  <div className="text-gray-500">Loading…</div>
                ) : selected.path.endsWith(".json") ? (
                  <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-gray-300">{content}</pre>
                ) : (
                  <MarkdownViewer content={content} />
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
