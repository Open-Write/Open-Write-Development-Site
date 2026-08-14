import { useCallback, useEffect, useState } from "react";
import { api, type FileNode } from "../api";

// ── File icon mapping ───────────────────────────────────────────────────
function fileIcon(node: FileNode): string {
  if (node.type === "directory") return "📁";
  const ext = node.name.split(".").pop()?.toLowerCase() || "";
  if (["md"].includes(ext)) return "📝";
  if (["txt", "rtf"].includes(ext)) return "📄";
  if (["json", "yaml", "yml", "toml"].includes(ext)) return "⚙️";
  if (["py", "js", "ts", "tsx", "jsx"].includes(ext)) return "🔧";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "🖼️";
  if (["pdf"].includes(ext)) return "📕";
  if (["html", "htm", "xml"].includes(ext)) return "🌐";
  if (["csv", "tsv"].includes(ext)) return "📊";
  return "📄";
}

// ── Context menu ────────────────────────────────────────────────────────
interface ContextMenuState {
  x: number;
  y: number;
  node: FileNode;
}

// ── Tree item ───────────────────────────────────────────────────────────
function TreeItem({
  node,
  depth,
  onOpen,
  onContext,
  expandedDirs,
  toggleDir,
}: {
  node: FileNode;
  depth: number;
  onOpen: (node: FileNode) => void;
  onContext: (e: React.MouseEvent, node: FileNode) => void;
  expandedDirs: Set<string>;
  toggleDir: (path: string) => void;
}) {
  const isDir = node.type === "directory";
  const isExpanded = expandedDirs.has(node.path);
  const paddingLeft = 8 + depth * 16;

  return (
    <>
      <div
        className="flex cursor-pointer items-center gap-1.5 rounded px-2 py-1 text-xs text-gray-300 transition-colors hover:bg-ink-800"
        style={{ paddingLeft }}
        onClick={() => {
          if (isDir) toggleDir(node.path);
          else onOpen(node);
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          onContext(e, node);
        }}
        title={node.path}
      >
        {isDir && (
          <span className="w-3 text-center text-gray-500">
            {isExpanded ? "▾" : "▸"}
          </span>
        )}
        {!isDir && <span className="w-3" />}
        <span className="text-sm">{fileIcon(node)}</span>
        <span className="truncate">{node.name}</span>
        {!isDir && node.size !== undefined && (
          <span className="ml-auto text-[10px] text-gray-600">
            {formatSize(node.size)}
          </span>
        )}
      </div>
      {isDir && isExpanded && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              onOpen={onOpen}
              onContext={onContext}
              expandedDirs={expandedDirs}
              toggleDir={toggleDir}
            />
          ))}
        </div>
      )}
    </>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

// ── ProjectFileTree ─────────────────────────────────────────────────────
interface Props {
  projectId: string;
  onOpenFile: (node: FileNode) => void;
}

export default function ProjectFileTree({ projectId, onOpenFile }: Props) {
  const [tree, setTree] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showNewItem, setShowNewItem] = useState<"file" | "dir" | null>(null);
  const [newItemPath, setNewItemPath] = useState("");
  const [newItemParent, setNewItemParent] = useState("");

  const loadTree = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.fileTree(projectId);
      setTree(res.tree);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    loadTree();
  }, [loadTree]);

  // Auto-expand first two levels on load.
  useEffect(() => {
    if (tree.length === 0) return;
    const dirs = new Set<string>();
    const walk = (nodes: FileNode[], depth: number) => {
      for (const n of nodes) {
        if (n.type === "directory" && depth < 2) {
          dirs.add(n.path);
          if (n.children) walk(n.children, depth + 1);
        }
      }
    };
    walk(tree, 0);
    setExpandedDirs(dirs);
  }, [tree]);

  const toggleDir = useCallback((path: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // ── Context menu actions ──────────────────────────────────────────────
  const handleContext = useCallback((e: React.MouseEvent, node: FileNode) => {
    setContextMenu({ x: e.clientX, y: e.clientY, node });
  }, []);

  const closeContext = useCallback(() => setContextMenu(null), []);

  useEffect(() => {
    const handler = () => closeContext();
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, [closeContext]);

  const handleDelete = useCallback(async () => {
    if (!contextMenu) return;
    const name = contextMenu.node.name;
    if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
    try {
      await api.deleteFileItem(projectId, contextMenu.node.path);
      loadTree();
    } catch (e) {
      alert((e as Error).message);
    }
    closeContext();
  }, [contextMenu, projectId, loadTree, closeContext]);

  const handleNewFile = useCallback(() => {
    if (!contextMenu) return;
    const parent =
      contextMenu.node.type === "directory"
        ? contextMenu.node.path
        : contextMenu.node.path.split("/").slice(0, -1).join("/");
    setNewItemParent(parent);
    setNewItemPath("");
    setShowNewItem("file");
    closeContext();
  }, [contextMenu, closeContext]);

  const handleNewDir = useCallback(() => {
    if (!contextMenu) return;
    const parent =
      contextMenu.node.type === "directory"
        ? contextMenu.node.path
        : contextMenu.node.path.split("/").slice(0, -1).join("/");
    setNewItemParent(parent);
    setNewItemPath("");
    setShowNewItem("dir");
    closeContext();
  }, [contextMenu, closeContext]);

  const confirmNewItem = useCallback(async () => {
    if (!newItemPath.trim()) return;
    const fullPath = newItemParent
      ? `${newItemParent}/${newItemPath.trim()}`
      : newItemPath.trim();
    try {
      await api.createFileItem(projectId, {
        path: fullPath,
        is_directory: showNewItem === "dir",
        content: showNewItem === "file" ? "" : undefined,
      });
      loadTree();
    } catch (e) {
      alert((e as Error).message);
    }
    setShowNewItem(null);
  }, [newItemPath, newItemParent, showNewItem, projectId, loadTree]);

  // ── Search filter ─────────────────────────────────────────────────────
  const filterTree = useCallback(
    (nodes: FileNode[], query: string): FileNode[] => {
      if (!query) return nodes;
      const q = query.toLowerCase();
      const result: FileNode[] = [];
      for (const n of nodes) {
        if (n.type === "directory") {
          const filtered = n.children ? filterTree(n.children, query) : [];
          if (filtered.length > 0 || n.name.toLowerCase().includes(q)) {
            result.push({ ...n, children: filtered });
          }
        } else if (n.name.toLowerCase().includes(q)) {
          result.push(n);
        }
      }
      return result;
    },
    [],
  );

  const displayTree = searchQuery ? filterTree(tree, searchQuery) : tree;

  // ── Expand all / collapse all ─────────────────────────────────────────
  const expandAll = useCallback(() => {
    const dirs = new Set<string>();
    const walk = (nodes: FileNode[]) => {
      for (const n of nodes) {
        if (n.type === "directory") {
          dirs.add(n.path);
          if (n.children) walk(n.children);
        }
      }
    };
    walk(tree);
    setExpandedDirs(dirs);
  }, [tree]);

  const collapseAll = useCallback(() => setExpandedDirs(new Set()), []);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-edge px-3 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          Project Files
        </h3>
        <div className="flex items-center gap-1">
          <button
            className="text-xs text-gray-500 hover:text-gray-300"
            onClick={expandAll}
            title="Expand all"
          >
            ⊞
          </button>
          <button
            className="text-xs text-gray-500 hover:text-gray-300"
            onClick={collapseAll}
            title="Collapse all"
          >
            ⊟
          </button>
          <button
            className="text-xs text-gray-500 hover:text-gray-300"
            onClick={loadTree}
            title="Refresh"
          >
            ↻
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="border-b border-edge px-3 py-2">
        <input
          className="input !py-1 text-xs"
          placeholder="Search files…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
      </div>

      {/* Quick actions */}
      <div className="flex gap-1 border-b border-edge px-3 py-1.5">
        <button
          className="rounded px-2 py-0.5 text-[10px] text-gray-500 hover:bg-ink-800 hover:text-gray-300"
          onClick={() => {
            setNewItemParent("");
            setNewItemPath("");
            setShowNewItem("file");
          }}
        >
          + File
        </button>
        <button
          className="rounded px-2 py-0.5 text-[10px] text-gray-500 hover:bg-ink-800 hover:text-gray-300"
          onClick={() => {
            setNewItemParent("");
            setNewItemPath("");
            setShowNewItem("dir");
          }}
        >
          + Folder
        </button>
      </div>

      {/* New item input */}
      {showNewItem && (
        <div className="border-b border-edge bg-ink-900 px-3 py-2">
          <div className="mb-1 text-[10px] text-gray-500">
            {showNewItem === "file" ? "New file" : "New folder"}
            {newItemParent && ` in ${newItemParent}`}
          </div>
          <div className="flex gap-1">
            <input
              className="input !py-1 text-xs"
              placeholder={showNewItem === "file" ? "filename.md" : "folder name"}
              value={newItemPath}
              onChange={(e) => setNewItemPath(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && confirmNewItem()}
              autoFocus
            />
            <button
              className="btn-primary !py-1 !px-2 text-xs"
              onClick={confirmNewItem}
            >
              Create
            </button>
            <button
              className="btn-ghost !py-1 !px-2 text-xs"
              onClick={() => setShowNewItem(null)}
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {loading && (
          <div className="px-3 py-2 text-xs text-gray-500">Loading…</div>
        )}
        {error && (
          <div className="px-3 py-2 text-xs text-red-400">{error}</div>
        )}
        {!loading && displayTree.length === 0 && (
          <div className="px-3 py-2 text-xs text-gray-500">
            {searchQuery ? "No matching files." : "Empty project."}
          </div>
        )}
        {displayTree.map((node) => (
          <TreeItem
            key={node.path}
            node={node}
            depth={0}
            onOpen={onOpenFile}
            onContext={handleContext}
            expandedDirs={expandedDirs}
            toggleDir={toggleDir}
          />
        ))}
      </div>

      {/* Context menu */}
      {contextMenu && (
        <div
          className="fixed z-50 min-w-[120px] rounded-lg border border-edge bg-ink-850 py-1 shadow-xl"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="w-full px-3 py-1.5 text-left text-xs text-gray-300 hover:bg-ink-700"
            onClick={() => {
              onOpenFile(contextMenu.node);
              closeContext();
            }}
          >
            Open
          </button>
          {contextMenu.node.type === "directory" && (
            <>
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-gray-300 hover:bg-ink-700"
                onClick={handleNewFile}
              >
                New file here
              </button>
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-gray-300 hover:bg-ink-700"
                onClick={handleNewDir}
              >
                New folder here
              </button>
            </>
          )}
          <div className="my-1 border-t border-edge" />
          <button
            className="w-full px-3 py-1.5 text-left text-xs text-red-400 hover:bg-ink-700"
            onClick={handleDelete}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}
