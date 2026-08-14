import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

// ── Types ───────────────────────────────────────────────────────────────
interface OpenFile {
  path: string;
  content: string;
  kind: "text" | "image" | "binary";
  dirty: boolean;
  wordCount: number;
}

interface Props {
  projectId: string;
  openFiles: OpenFile[];
  activeFile: string | null;
  onFileContentChange: (path: string, content: string) => void;
  onFileSave: (path: string) => void;
  onCloseFile: (path: string) => void;
  onSelectFile: (path: string) => void;
}

// ── Toolbar button ──────────────────────────────────────────────────────
function ToolbarBtn({
  active,
  disabled,
  onClick,
  title,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={title}
      className={[
        "flex h-7 w-7 items-center justify-center rounded text-xs font-bold transition-colors",
        active
          ? "bg-accent-soft/20 text-accent"
          : "text-gray-400 hover:bg-ink-700 hover:text-gray-200",
        disabled ? "opacity-30 cursor-not-allowed" : "",
      ].join(" ")}
    >
      {children}
    </button>
  );
}

function Sep() {
  return <div className="mx-1 h-5 w-px bg-edge" />;
}

// ── Markdown-to-HTML conversion (simple) ────────────────────────────────
function mdToHtml(md: string): string {
  let html = md;
  // Headings
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
  // Bold + italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<b><i>$1</i></b>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  html = html.replace(/\*(.+?)\*/g, "<i>$1</i>");
  html = html.replace(/___(.+?)___/g, "<b><i>$1</i></b>");
  html = html.replace(/__(.+?)__/g, "<b>$1</b>");
  html = html.replace(/_(.+?)_/g, "<i>$1</i>");
  // Strikethrough
  html = html.replace(/~~(.+?)~~/g, "<s>$1</s>");
  // Blockquote
  html = html.replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>");
  // Horizontal rule
  html = html.replace(/^---$/gm, "<hr>");
  // Unordered list
  html = html.replace(/^[*\-+] (.+)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
  // Paragraphs (lines not already wrapped)
  html = html.replace(/^(?!<[a-z])(.*\S.*)$/gm, "<p>$1</p>");
  // Clean up double paragraph wraps
  html = html.replace(/<p><\/p>/g, "");
  // Line breaks
  html = html.replace(/\n/g, "");
  return html;
}

function htmlToMd(html: string): string {
  let md = html;
  // Headings
  md = md.replace(/<h1[^>]*>(.*?)<\/h1>/gi, "# $1\n");
  md = md.replace(/<h2[^>]*>(.*?)<\/h2>/gi, "## $1\n");
  md = md.replace(/<h3[^>]*>(.*?)<\/h3>/gi, "### $1\n");
  md = md.replace(/<h4[^>]*>(.*?)<\/h4>/gi, "#### $1\n");
  md = md.replace(/<h5[^>]*>(.*?)<\/h5>/gi, "##### $1\n");
  md = md.replace(/<h6[^>]*>(.*?)<\/h6>/gi, "###### $1\n");
  // Bold + italic combos
  md = md.replace(/<b><i>(.*?)<\/i><\/b>/gi, "***$1***");
  md = md.replace(/<strong><em>(.*?)<\/em><strong>/gi, "***$1***");
  md = md.replace(/<i><b>(.*?)<\/b><\/i>/gi, "***$1***");
  // Bold
  md = md.replace(/<b>(.*?)<\/b>/gi, "**$1**");
  md = md.replace(/<strong>(.*?)<\/strong>/gi, "**$1**");
  // Italic
  md = md.replace(/<i>(.*?)<\/i>/gi, "*$1*");
  md = md.replace(/<em>(.*?)<\/em>/gi, "*$1*");
  // Strikethrough
  md = md.replace(/<s>(.*?)<\/s>/gi, "~~$1~~");
  md = md.replace(/<del>(.*?)<\/del>/gi, "~~$1~~");
  // Underline (strip — markdown has no underline)
  md = md.replace(/<u>(.*?)<\/u>/gi, "$1");
  // Links
  md = md.replace(/<a[^>]*href="([^"]*)"[^>]*>(.*?)<\/a>/gi, "[$2]($1)");
  // Images
  md = md.replace(/<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*\/?>/gi, "![$2]($1)");
  md = md.replace(/<img[^>]*src="([^"]*)"[^>]*\/?>/gi, "![]($1)");
  // Blockquote
  md = md.replace(/<blockquote[^>]*>(.*?)<\/blockquote>/gi, "> $1\n");
  // Lists
  md = md.replace(/<li[^>]*>(.*?)<\/li>/gi, "- $1\n");
  md = md.replace(/<\/?[uo]l[^>]*>/gi, "");
  // Horizontal rule
  md = md.replace(/<hr[^>]*\/?>/gi, "---\n");
  // Paragraphs
  md = md.replace(/<p[^>]*>(.*?)<\/p>/gi, "$1\n");
  // Line breaks
  md = md.replace(/<br\s*\/?>/gi, "\n");
  // Strip remaining tags
  md = md.replace(/<\/?[^>]+(>|$)/g, "");
  // Decode entities
  md = md.replace(/&amp;/g, "&");
  md = md.replace(/&lt;/g, "<");
  md = md.replace(/&gt;/g, ">");
  md = md.replace(/&quot;/g, '"');
  md = md.replace(/&#39;/g, "'");
  // Clean up excessive newlines
  md = md.replace(/\n{3,}/g, "\n\n");
  return md.trim();
}

// ── DocumentEditor ──────────────────────────────────────────────────────
export default function DocumentEditor({
  projectId,
  openFiles,
  activeFile,
  onFileContentChange,
  onFileSave,
  onCloseFile,
  onSelectFile,
}: Props) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [viewMode, setViewMode] = useState<"rich" | "source">("rich");
  const [sourceContent, setSourceContent] = useState("");
  const [showFindReplace, setShowFindReplace] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [replaceWith, setReplaceWith] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const [status, setStatus] = useState("");
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const currentFile = openFiles.find((f) => f.path === activeFile) || null;

  // ── Sync editor content when active file changes ──────────────────────
  useEffect(() => {
    if (!currentFile || !editorRef.current) return;
    if (viewMode === "rich") {
      if (currentFile.kind === "text") {
        editorRef.current.innerHTML = mdToHtml(currentFile.content);
      }
    } else {
      setSourceContent(currentFile.content);
    }
  }, [activeFile, viewMode]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Content change handler ────────────────────────────────────────────
  const handleRichInput = useCallback(() => {
    if (!editorRef.current || !currentFile) return;
    const html = editorRef.current.innerHTML;
    const md = htmlToMd(html);
    onFileContentChange(currentFile.path, md);
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(async () => {
      try {
        await api.saveFile(projectId, { path: currentFile.path, content: md });
        setStatus("Autosaved");
      } catch { /* silent */ }
    }, 3000);
  }, [currentFile, onFileContentChange, projectId]);

  const handleSourceChange = useCallback(
    (value: string) => {
      setSourceContent(value);
      if (currentFile) {
        onFileContentChange(currentFile.path, value);
        if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
        autosaveTimer.current = setTimeout(async () => {
          try {
            await api.saveFile(projectId, { path: currentFile.path, content: value });
            setStatus("Autosaved");
          } catch { /* silent */ }
        }, 3000);
      }
    },
    [currentFile, onFileContentChange, projectId],
  );

  useEffect(() => {
    return () => {
      if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    };
  }, []);

  // ── Explicit save ─────────────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    if (!currentFile) return;
    try {
      const content =
        viewMode === "source" ? sourceContent : currentFile.content;
      await api.saveFile(projectId, { path: currentFile.path, content });
      onFileSave(currentFile.path);
      setStatus("Saved");
    } catch {
      setStatus("Save failed");
    }
  }, [currentFile, viewMode, sourceContent, projectId, onFileSave]);

  // ── Keyboard shortcuts ────────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        handleSave();
      }
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
  }, [handleSave, showFindReplace]);

  // ── Rich text commands ────────────────────────────────────────────────
  const exec = (cmd: string, value?: string) => {
    document.execCommand(cmd, false, value);
    editorRef.current?.focus();
    handleRichInput();
  };

  // ── Find & Replace (works on source content) ─────────────────────────
  const doReplaceAll = () => {
    if (!findQuery || !currentFile) return;
    const flags = matchCase ? "g" : "gi";
    const re = new RegExp(
      findQuery.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
      flags,
    );
    const content = viewMode === "source" ? sourceContent : currentFile.content;
    const next = content.replace(re, replaceWith);
    if (viewMode === "source") {
      handleSourceChange(next);
    } else {
      onFileContentChange(currentFile.path, next);
      if (editorRef.current) editorRef.current.innerHTML = mdToHtml(next);
    }
  };

  // ── Word count ────────────────────────────────────────────────────────
  const wordCount = currentFile
    ? (currentFile.content.trim().match(/\S+/g) || []).length
    : 0;

  // ── Render ────────────────────────────────────────────────────────────
  if (!currentFile) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-gray-500">
        Open a file from the project tree to start editing.
      </div>
    );
  }

  const isMarkdown =
    currentFile.path.endsWith(".md") || currentFile.path.endsWith(".txt");

  return (
    <div className="flex h-full flex-col">
      {/* ── Tab bar ──────────────────────────────────────────────────── */}
      <div className="flex items-center gap-0 overflow-x-auto border-b border-edge bg-ink-900">
        {openFiles.map((f) => (
          <div
            key={f.path}
            className={[
              "group flex items-center gap-1.5 border-r border-edge px-3 py-2 text-xs cursor-pointer transition-colors",
              f.path === activeFile
                ? "bg-ink-850 text-gray-100"
                : "text-gray-500 hover:bg-ink-800 hover:text-gray-300",
            ].join(" ")}
            onClick={() => onSelectFile(f.path)}
          >
            <span className="max-w-[10rem] truncate">
              {f.path.split("/").pop()}
            </span>
            {f.dirty && (
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
            )}
            <button
              className="ml-1 text-gray-600 opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
              onClick={(e) => {
                e.stopPropagation();
                onCloseFile(f.path);
              }}
              title="Close"
            >
              ×
            </button>
          </div>
        ))}
      </div>

      {/* ── Toolbar ──────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-1 border-b border-edge bg-ink-900 px-3 py-1.5">
        <ToolbarBtn onClick={() => exec("bold")} title="Bold (Ctrl+B)">
          <span className="font-bold">B</span>
        </ToolbarBtn>
        <ToolbarBtn onClick={() => exec("italic")} title="Italic (Ctrl+I)">
          <span className="italic">I</span>
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("underline")}
          title="Underline (Ctrl+U)"
        >
          <span className="underline">U</span>
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("strikeThrough")}
          title="Strikethrough"
        >
          <span className="line-through">S</span>
        </ToolbarBtn>
        <Sep />
        <ToolbarBtn
          onClick={() => exec("formatBlock", "h1")}
          title="Heading 1"
        >
          H1
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("formatBlock", "h2")}
          title="Heading 2"
        >
          H2
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("formatBlock", "h3")}
          title="Heading 3"
        >
          H3
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("formatBlock", "p")}
          title="Paragraph"
        >
          P
        </ToolbarBtn>
        <Sep />
        <ToolbarBtn
          onClick={() => exec("insertUnorderedList")}
          title="Bullet list"
        >
          •≡
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("insertOrderedList")}
          title="Numbered list"
        >
          1.
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("formatBlock", "blockquote")}
          title="Blockquote"
        >
          "
        </ToolbarBtn>
        <Sep />
        <ToolbarBtn
          onClick={() => exec("justifyLeft")}
          title="Align left"
        >
          ≡←
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("justifyCenter")}
          title="Align center"
        >
          ≡↔
        </ToolbarBtn>
        <ToolbarBtn
          onClick={() => exec("justifyRight")}
          title="Align right"
        >
          ≡→
        </ToolbarBtn>
        <Sep />
        <ToolbarBtn onClick={() => exec("undo")} title="Undo (Ctrl+Z)">
          ↶
        </ToolbarBtn>
        <ToolbarBtn onClick={() => exec("redo")} title="Redo (Ctrl+Y)">
          ↷
        </ToolbarBtn>
        <Sep />
        {isMarkdown && (
          <button
            className={[
              "rounded px-2 py-1 text-xs font-medium transition-colors",
              viewMode === "source"
                ? "bg-accent-soft/20 text-accent"
                : "text-gray-400 hover:bg-ink-700 hover:text-gray-200",
            ].join(" ")}
            onClick={() => setViewMode(viewMode === "rich" ? "source" : "rich")}
            title="Toggle markdown source view"
          >
            {viewMode === "rich" ? "⟨/⟩" : "Rich"}
          </button>
        )}
        <Sep />
        <button
          className="rounded px-2 py-1 text-xs text-gray-400 hover:bg-ink-700 hover:text-gray-200"
          onClick={() => setShowFindReplace(!showFindReplace)}
          title="Find & Replace (Ctrl+F)"
        >
          🔍
        </button>
        <div className="ml-auto flex items-center gap-3 text-xs text-gray-500">
          <span>{wordCount.toLocaleString()} words</span>
          {currentFile.dirty && (
            <span className="text-amber-400">Unsaved</span>
          )}
          {status && <span className="text-emerald-400">{status}</span>}
          <button
            className="btn-primary !py-1 !px-3 text-xs"
            onClick={handleSave}
            disabled={!currentFile.dirty}
          >
            Save
          </button>
        </div>
      </div>

      {/* ── Find / Replace bar ───────────────────────────────────────── */}
      {showFindReplace && (
        <div className="flex flex-wrap items-center gap-2 border-b border-edge bg-ink-900 px-3 py-2">
          <input
            className="input !w-44"
            placeholder="Find…"
            value={findQuery}
            onChange={(e) => setFindQuery(e.target.value)}
            autoFocus
          />
          <input
            className="input !w-44"
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
          <button
            className="btn-ghost !py-1 !px-2 text-xs"
            onClick={doReplaceAll}
            disabled={!findQuery}
          >
            Replace All
          </button>
          <button
            className="btn-ghost !py-1 !px-2 text-xs"
            onClick={() => setShowFindReplace(false)}
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Editor area ──────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto">
        {currentFile.kind === "image" ? (
          <div className="flex h-full items-center justify-center p-8">
            <img
              src={currentFile.content}
              alt={currentFile.path}
              className="max-h-full max-w-full rounded-lg border border-edge"
            />
          </div>
        ) : currentFile.kind === "binary" ? (
          <div className="flex h-full items-center justify-center text-sm text-gray-500">
            Binary file — cannot be edited in the browser.
          </div>
        ) : viewMode === "source" ? (
          <textarea
            className="h-full w-full resize-none border-none bg-transparent p-4 font-mono text-sm text-gray-200 focus:outline-none"
            value={sourceContent}
            onChange={(e) => handleSourceChange(e.target.value)}
            spellCheck={false}
          />
        ) : (
          <div
            ref={editorRef}
            className="prose-content h-full w-full p-6 focus:outline-none"
            contentEditable
            suppressContentEditableWarning
            onInput={handleRichInput}
            onBlur={handleRichInput}
            spellCheck
          />
        )}
      </div>
    </div>
  );
}
