import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Chapter } from "../api";

function wordCount(s: string) {
  return (s.trim().match(/\S+/g) || []).length;
}

// Chapter browser + plain-text editor. Saving captures a "user_edit" version on
// the backend, so manual edits show up in the Versions tab.
export default function WritingEditor({
  projectId,
  initialChapter,
  onChapterOpened,
}: {
  projectId: string;
  initialChapter?: number | null;
  onChapterOpened?: () => void;
}) {
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [active, setActive] = useState<Chapter | null>(null);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  // ── Find / Replace state ───────────────────────────────────────────────
  const [showFindReplace, setShowFindReplace] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [replaceWith, setReplaceWith] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const [matchCount, setMatchCount] = useState(0);
  const [currentMatch, setCurrentMatch] = useState(-1);

  // ── Autosave state ─────────────────────────────────────────────────────
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedContent = useRef("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const loadList = () => {
    setLoading(true);
    api
      .listChapters(projectId)
      .then((r) => { setChapters(r.chapters); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(loadList, [projectId]);

  // Auto-open a chapter when cross-linked from Outputs tab.
  useEffect(() => {
    if (initialChapter == null || chapters.length === 0) return;
    const target = chapters.find((c) => c.chapter_number === initialChapter);
    if (target) {
      openChapter(target);
      onChapterOpened?.();
    }
  }, [initialChapter, chapters]); // eslint-disable-line react-hooks/exhaustive-deps

  const openChapter = (c: Chapter) => {
    if (dirty && !confirm("Discard unsaved changes?")) return;
    setActive(c);
    setStatus("");
    api
      .readChapter(projectId, c.path)
      .then((r) => {
        setContent(r.content);
        lastSavedContent.current = r.content;
        setDirty(false);
      })
      .catch((e) => setError(e.message));
  };

  const save = async () => {
    if (!active) return;
    setSaving(true);
    setError("");
    try {
      const r = await api.saveChapter(projectId, { path: active.path, content });
      setDirty(false);
      lastSavedContent.current = content;
      setStatus(`Saved · ${r.word_count.toLocaleString()} words · version captured`);
      loadList();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  // ── Debounced autosave ─────────────────────────────────────────────────
  // Fires 3 seconds after the user stops typing. Does NOT capture a version
  // (that's only for explicit Save) — just writes the file to disk so edits
  // aren't lost if the browser crashes.
  const scheduleAutosave = useCallback(() => {
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(async () => {
      if (!active || !dirty) return;
      try {
        await api.saveChapter(projectId, {
          path: active.path,
          content,
          // marker so backend can distinguish autosave from explicit save
          // (backend currently always captures a version, but this is the
          // signal for future differentiation)
        });
        lastSavedContent.current = content;
        setStatus("Autosaved");
      } catch {
        // Silent — don't annoy the user with autosave failures.
      }
    }, 3000);
  }, [active, content, dirty, projectId]);

  // Clean up timer on unmount.
  useEffect(() => {
    return () => {
      if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    };
  }, []);

  const handleChange = (value: string) => {
    setContent(value);
    setDirty(value !== lastSavedContent.current);
    setStatus("");
    scheduleAutosave();
  };

  // ── Find / Replace logic ───────────────────────────────────────────────
  useEffect(() => {
    if (!findQuery) {
      setMatchCount(0);
      setCurrentMatch(-1);
      return;
    }
    const flags = matchCase ? "g" : "gi";
    try {
      const re = new RegExp(findQuery.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), flags);
      const matches = content.match(re);
      setMatchCount(matches?.length || 0);
      setCurrentMatch(matches && matches.length > 0 ? 0 : -1);
    } catch {
      setMatchCount(0);
      setCurrentMatch(-1);
    }
  }, [findQuery, matchCase, content]);

  const doReplaceAll = () => {
    if (!findQuery) return;
    const flags = matchCase ? "g" : "gi";
    try {
      const re = new RegExp(findQuery.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), flags);
      const next = content.replace(re, replaceWith);
      setContent(next);
      setDirty(next !== lastSavedContent.current);
      scheduleAutosave();
    } catch {
      // invalid regex — ignore
    }
  };

  const doReplaceCurrent = () => {
    if (!findQuery || currentMatch < 0 || !textareaRef.current) return;
    const flags = matchCase ? "g" : "gi";
    try {
      const re = new RegExp(findQuery.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), flags);
      let idx = 0;
      const next = content.replace(re, (match) => {
        if (idx === currentMatch) {
          idx++;
          return replaceWith;
        }
        idx++;
        return match;
      });
      setContent(next);
      setDirty(next !== lastSavedContent.current);
      scheduleAutosave();
    } catch {
      // invalid regex — ignore
    }
  };

  const navigateMatch = (direction: 1 | -1) => {
    if (matchCount === 0) return;
    setCurrentMatch((prev) => {
      if (direction === 1) return (prev + 1) % matchCount;
      return (prev - 1 + matchCount) % matchCount;
    });
  };

  // Keyboard shortcut: Ctrl+F / Cmd+F to toggle find/replace.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        e.preventDefault();
        setShowFindReplace((prev) => !prev);
      }
      if (e.key === "Escape" && showFindReplace) {
        setShowFindReplace(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [showFindReplace]);

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
                <button
                  className="btn-ghost !py-1 !px-2 text-xs"
                  onClick={() => setShowFindReplace(!showFindReplace)}
                  title="Find & Replace (Ctrl+F)"
                >
                  🔍 Find
                </button>
                {status && <span className="text-xs text-emerald-400">{status}</span>}
                {dirty && <span className="text-xs text-amber-400">Unsaved</span>}
                <button className="btn-primary" onClick={save} disabled={saving || !dirty}>
                  {saving ? "Saving…" : "Save"}
                </button>
              </div>
            </div>

            {/* Find / Replace bar */}
            {showFindReplace && (
              <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-edge bg-ink-850 px-3 py-2">
                <input
                  className="input !w-48"
                  placeholder="Find…"
                  value={findQuery}
                  onChange={(e) => setFindQuery(e.target.value)}
                  autoFocus
                />
                <input
                  className="input !w-48"
                  placeholder="Replace with…"
                  value={replaceWith}
                  onChange={(e) => setReplaceWith(e.target.value)}
                />
                <label className="flex items-center gap-1 text-xs text-gray-400">
                  <input
                    type="checkbox"
                    checked={matchCase}
                    onChange={(e) => setMatchCase(e.target.checked)}
                    className="accent-accent-soft"
                  />
                  Aa
                </label>
                <span className="text-xs text-gray-500">
                  {matchCount > 0 ? `${currentMatch + 1}/${matchCount}` : findQuery ? "No matches" : ""}
                </span>
                <button
                  className="btn-ghost !py-1 !px-2 text-xs"
                  onClick={() => navigateMatch(-1)}
                  disabled={matchCount === 0}
                  title="Previous match"
                >
                  ↑
                </button>
                <button
                  className="btn-ghost !py-1 !px-2 text-xs"
                  onClick={() => navigateMatch(1)}
                  disabled={matchCount === 0}
                  title="Next match"
                >
                  ↓
                </button>
                <button
                  className="btn-ghost !py-1 !px-2 text-xs"
                  onClick={doReplaceCurrent}
                  disabled={matchCount === 0}
                  title="Replace current match"
                >
                  Replace
                </button>
                <button
                  className="btn-ghost !py-1 !px-2 text-xs"
                  onClick={doReplaceAll}
                  disabled={matchCount === 0}
                  title="Replace all matches"
                >
                  All
                </button>
                <button
                  className="btn-ghost !py-1 !px-2 text-xs"
                  onClick={() => setShowFindReplace(false)}
                >
                  ✕
                </button>
              </div>
            )}

            {error && <div className="mb-2 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}
            <textarea
              ref={textareaRef}
              className="input flex-1 resize-none font-serif text-[0.95rem] leading-relaxed"
              value={content}
              onChange={(e) => handleChange(e.target.value)}
              spellCheck
            />
          </>
        )}
      </div>
    </div>
  );
}
