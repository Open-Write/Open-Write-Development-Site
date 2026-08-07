import { useEffect, useState } from "react";
import { api, type Chapter } from "../api";

function wordCount(s: string) {
  return (s.trim().match(/\S+/g) || []).length;
}

// Chapter browser + plain-text editor. Saving captures a "user_edit" version on
// the backend, so manual edits show up in the Versions tab.
export default function WritingEditor({ projectId }: { projectId: string }) {
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [active, setActive] = useState<Chapter | null>(null);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const loadList = () => {
    setLoading(true);
    api
      .listChapters(projectId)
      .then((r) => { setChapters(r.chapters); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(loadList, [projectId]);

  const openChapter = (c: Chapter) => {
    if (dirty && !confirm("Discard unsaved changes?")) return;
    setActive(c);
    setStatus("");
    api
      .readChapter(projectId, c.path)
      .then((r) => { setContent(r.content); setDirty(false); })
      .catch((e) => setError(e.message));
  };

  const save = async () => {
    if (!active) return;
    setSaving(true);
    setError("");
    try {
      const r = await api.saveChapter(projectId, { path: active.path, content });
      setDirty(false);
      setStatus(`Saved · ${r.word_count.toLocaleString()} words · version captured`);
      loadList();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-[calc(100vh-11rem)] gap-6">
      <div className="w-64 shrink-0 space-y-3 overflow-y-auto">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-300">Chapters</h3>
          <button className="btn-ghost !py-1 !px-2 text-xs" onClick={loadList}>Refresh</button>
        </div>
        {loading && <div className="text-sm text-gray-500">Loading…</div>}
        {!loading && chapters.length === 0 && (
          <div className="card p-4 text-center text-xs text-gray-500">
            No chapters yet. Run the pipeline through the chapter phase to generate prose.
          </div>
        )}
        {chapters.map((c) => (
          <button
            key={c.path}
            onClick={() => openChapter(c)}
            className={[
              "block w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors",
              active?.path === c.path
                ? "border-accent-soft bg-accent-soft/10 text-gray-100"
                : "border-edge bg-ink-850 text-gray-300 hover:bg-ink-800",
            ].join(" ")}
          >
            <div className="truncate font-medium">{c.title || c.filename}</div>
            <div className="text-xs text-gray-500">{c.word_count.toLocaleString()} words</div>
          </button>
        ))}
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        {!active ? (
          <div className="card flex flex-1 items-center justify-center text-sm text-gray-500">
            Select a chapter to read or edit.
          </div>
        ) : (
          <>
            <div className="mb-3 flex items-center gap-3">
              <h2 className="truncate text-lg font-semibold text-gray-100">{active.title || active.filename}</h2>
              <span className="text-xs text-gray-500">{wordCount(content).toLocaleString()} words</span>
              <div className="ml-auto flex items-center gap-3">
                {status && <span className="text-xs text-emerald-400">{status}</span>}
                {dirty && <span className="text-xs text-amber-400">Unsaved</span>}
                <button className="btn-primary" onClick={save} disabled={saving || !dirty}>
                  {saving ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
            {error && <div className="mb-2 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}
            <textarea
              className="input flex-1 resize-none font-serif text-[0.95rem] leading-relaxed"
              value={content}
              onChange={(e) => { setContent(e.target.value); setDirty(true); setStatus(""); }}
              spellCheck
            />
          </>
        )}
      </div>
    </div>
  );
}
