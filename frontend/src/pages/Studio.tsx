import { useCallback, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, type FileNode } from "../api";
import Layout from "../components/Layout";
import ProjectFileTree from "../components/ProjectFileTree";
import DocumentEditor from "../components/DocumentEditor";

interface OpenFile {
  path: string;
  content: string;
  kind: "text" | "image" | "binary";
  dirty: boolean;
  wordCount: number;
}

export default function Studio() {
  const { id = "" } = useParams();
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([]);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [sidebarWidth, setSidebarWidth] = useState(280);
  const [resizing, setResizing] = useState(false);

  // ── Open a file from the tree ─────────────────────────────────────────
  const handleOpenFile = useCallback(
    async (node: FileNode) => {
      if (node.type === "directory") return;
      // Already open? Just switch to it.
      if (openFiles.find((f) => f.path === node.path)) {
        setActiveFile(node.path);
        return;
      }
      try {
        setError("");
        const res = await api.readFile(id, node.path);
        setOpenFiles((prev) => [
          ...prev,
          {
            path: node.path,
            content: res.content || "",
            kind: res.kind as "text" | "image" | "binary",
            dirty: false,
            wordCount: res.word_count || 0,
          },
        ]);
        setActiveFile(node.path);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [id, openFiles],
  );

  // ── Content change ────────────────────────────────────────────────────
  const handleContentChange = useCallback((path: string, content: string) => {
    setOpenFiles((prev) =>
      prev.map((f) =>
        f.path === path
          ? {
              ...f,
              content,
              dirty: true,
              wordCount: (content.trim().match(/\S+/g) || []).length,
            }
          : f,
      ),
    );
  }, []);

  // ── File saved ────────────────────────────────────────────────────────
  const handleFileSave = useCallback((path: string) => {
    setOpenFiles((prev) =>
      prev.map((f) => (f.path === path ? { ...f, dirty: false } : f)),
    );
  }, []);

  // ── Close a tab ───────────────────────────────────────────────────────
  const handleCloseFile = useCallback(
    (path: string) => {
      const file = openFiles.find((f) => f.path === path);
      if (file?.dirty && !confirm(`"${path.split("/").pop()}" has unsaved changes. Close anyway?`))
        return;
      setOpenFiles((prev) => prev.filter((f) => f.path !== path));
      if (activeFile === path) {
        const remaining = openFiles.filter((f) => f.path !== path);
        setActiveFile(remaining.length > 0 ? remaining[remaining.length - 1].path : null);
      }
    },
    [openFiles, activeFile],
  );

  // ── Sidebar resize ────────────────────────────────────────────────────
  const handleResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setResizing(true);
      const startX = e.clientX;
      const startWidth = sidebarWidth;
      const onMove = (ev: MouseEvent) => {
        const delta = ev.clientX - startX;
        setSidebarWidth(Math.max(200, Math.min(500, startWidth + delta)));
      };
      const onUp = () => {
        setResizing(false);
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [sidebarWidth],
  );

  // ── Dirty count ───────────────────────────────────────────────────────
  const dirtyCount = openFiles.filter((f) => f.dirty).length;

  return (
    <Layout>
      {error && (
        <div className="mb-2 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Breadcrumb */}
      <div className="mb-3 flex items-center gap-2 text-xs text-gray-500">
        <Link to="/" className="hover:text-gray-300">
          Projects
        </Link>
        <span>/</span>
        <Link to={`/projects/${id}`} className="hover:text-gray-300">
          Project
        </Link>
        <span>/</span>
        <span className="text-gray-300">Studio</span>
        {dirtyCount > 0 && (
          <span className="ml-2 badge bg-amber-600/15 text-amber-300">
            {dirtyCount} unsaved
          </span>
        )}
        {openFiles.length > 0 && (
          <span className="ml-auto text-gray-600">
            {openFiles.length} file{openFiles.length !== 1 ? "s" : ""} open
          </span>
        )}
      </div>

      {/* Main workspace */}
      <div
        className="flex overflow-hidden rounded-xl border border-edge bg-panel"
        style={{ height: "calc(100vh - 10rem)" }}
      >
        {/* Sidebar — file tree */}
        <div
          className="shrink-0 overflow-hidden border-r border-edge bg-ink-900"
          style={{ width: sidebarWidth }}
        >
          <ProjectFileTree projectId={id} onOpenFile={handleOpenFile} />
        </div>

        {/* Resize handle */}
        <div
          className="w-1 cursor-col-resize bg-edge transition-colors hover:bg-accent-soft/30"
          onMouseDown={handleResizeStart}
        />

        {/* Editor area */}
        <div className="flex min-w-0 flex-1 flex-col">
          <DocumentEditor
            projectId={id}
            openFiles={openFiles}
            activeFile={activeFile}
            onFileContentChange={handleContentChange}
            onFileSave={handleFileSave}
            onCloseFile={handleCloseFile}
            onSelectFile={setActiveFile}
          />
        </div>
      </div>

      {/* Resize overlay (prevents iframe/pointer capture during drag) */}
      {resizing && <div className="fixed inset-0 z-50 cursor-col-resize" />}
    </Layout>
  );
}
