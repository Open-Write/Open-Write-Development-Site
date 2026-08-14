// Typed fetch wrapper with auth-header injection and error handling.
// All backend calls go through here so token management and error shaping
// live in one place.

const API_BASE = import.meta.env.VITE_API_BASE || "/api";
const TOKEN_KEY = "ow_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  opts: { method?: string; body?: unknown; auth?: boolean } = {}
): Promise<T> {
  const { method = "GET", body, auth = true } = opts;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && auth) {
    setToken(null);
    if (!location.pathname.startsWith("/studio/login")) location.href = "/studio/login";
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      // Non-JSON body (e.g. a plain-text 500/502/503 from infrastructure).
      // Don't let JSON.parse's SyntaxError bubble up as a confusing error —
      // use the raw text as the error message instead.
      data = null;
      if (!res.ok) {
        throw new ApiError(res.status, text.slice(0, 200) || res.statusText);
      }
    }
  }
  if (!res.ok) {
    const d = data as { detail?: unknown; message?: unknown } | null;
    const detail = d?.detail || d?.message || res.statusText;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

// ── Types ────────────────────────────────────────────────────────────────
export interface User { id: string; email: string; is_admin?: boolean; }
export interface Project {
  id: string; name: string; description: string; format: string;
  source_path?: string | null;
  created_at: string | null; updated_at: string | null;
}
export interface Provider {
  id: string; label: string; base_url: string; api_key: string; models: string[];
  key_source?: "user" | "openwrite" | "none";
}
export interface ProvidersConfig {
  providers: Provider[]; default_model: string; writer_model: string;
  critic_model: string; planner_model: string; audiobook_model: string;
  model_routing: Record<string, string>;
  server_key_providers?: string[];
}
export interface VersionSummary {
  id: string; phase: string; chapter_number: number | null; content_type: string;
  word_count: number | null; critic_verdict: string | null; created_at: string | null;
}
export interface VersionGroup {
  group: string; chapter_number: number | null;
  items: { content_type: string; versions: VersionSummary[] }[];
}
export interface VersionDetail extends VersionSummary {
  project_id: string; content: string; metadata: Record<string, unknown>;
}
export interface PhaseSpec { key: string; label: string; scope: string; gate?: boolean; }
export interface RunState {
  active: boolean; status?: string; current_phase?: string; current_phase_label?: string;
  current_unit_index?: number; units?: number[]; instructions?: string;
  last_error?: string | null; phase_results?: Record<string, unknown>;
  unit_results?: Record<string, unknown>; phase_order?: PhaseSpec[];
  revision_chapters?: number[]; revision_notes?: string;
  max_chapter_retries?: number;
  editorial_lock_retries?: number; max_editorial_lock_retries?: number;
  format?: string; unit_label?: string; unit_label_plural?: string;
}
export interface Chapter {
  path: string; filename: string; chapter_number: number | null;
  word_count: number; title: string;
}

// ── File browser types ──────────────────────────────────────────────────
export interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  kind?: "text" | "image" | "binary";
  size?: number;
  children?: FileNode[];
}

// ── Auth ─────────────────────────────────────────────────────────────────
export const api = {
  signup: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("/auth/signup", {
      method: "POST", body: { email, password }, auth: false,
    }),
  login: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("/auth/login", {
      method: "POST", body: { email, password }, auth: false,
    }),
  me: () => request<User>("/auth/me"),

  // Projects
  listProjects: () => request<Project[]>("/projects"),
  createProject: (name: string, description: string, format: string) =>
    request<Project>("/projects", { method: "POST", body: { name, description, format } }),
  importProject: (name: string, source_path: string, description: string, format: string) =>
    request<Project & { recognized_dirs?: string[]; file_count?: number }>(
      "/projects/import", { method: "POST", body: { name, source_path, description, format } }),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  updateProject: (id: string, body: { name?: string; description?: string }) =>
    request<Project>(`/projects/${id}`, { method: "PUT", body }),
  deleteProject: (id: string) =>
    request<{ deleted: boolean }>(`/projects/${id}`, { method: "DELETE" }),

  // Settings
  getProviders: () => request<ProvidersConfig>("/settings/providers"),
  updateProviders: (body: Partial<ProvidersConfig>) =>
    request<ProvidersConfig>("/settings/providers", { method: "PUT", body }),
  testConnection: (provider_id: string) =>
    request<{ ok: boolean; error?: string; model_count?: number }>(
      "/settings/test-connection", { method: "POST", body: { provider_id } }),
  listProviderModels: (provider_id: string) =>
    request<{ models: { id: string; name: string }[]; source: string }>(
      `/settings/models/${provider_id}`),
  getTokenUsage: () =>
    request<{ tokens_used: number; tokens_remaining: number; monthly_allowance: number; reset_date: string | null; tier: string; allowed_models: string[] | null }>(
      "/settings/token-usage"),
  getAccountTier: () =>
    request<{ tier: string; allowed_models: string[] | null; monthly_tokens: number }>(
      "/settings/account-tier"),

  // Pipeline
  runState: (pid: string) => request<RunState>(`/pipeline/${pid}/run-state`),
  phaseOrder: (pid: string) => request<{ phases: PhaseSpec[] }>(`/pipeline/${pid}/phase-order`),
  startRun: (pid: string, body: { instructions: string; word_floor?: number; rerun_mode?: string; max_chapter_retries?: number; max_editorial_lock_retries?: number }) =>
    request<RunState>(`/pipeline/${pid}/start-run`, { method: "POST", body }),
  advancePhase: (pid: string, body: { model_id?: string; instructions?: string } = {}) =>
    request<{ phase_started: boolean; current_phase: string; current_phase_label: string }>(
      `/pipeline/${pid}/advance-phase`, { method: "POST", body }),
  phaseTaskResult: (pid: string) =>
    request<Record<string, unknown>>(`/pipeline/${pid}/phase-task-result`),
  // Revision
  editorialReports: (pid: string) =>
    request<{ reports: { chapter: number; content: string | null }[] }>(
      `/pipeline/${pid}/editorial-reports`),
  startRevision: (pid: string, body: { chapters: number[]; revision_notes: string }) =>
    request<{ status: string; current_phase: string; revision_chapters: number[] }>(
      `/pipeline/${pid}/start-revision`, { method: "POST", body }),
  generateRevisionPlan: (pid: string, body: { feedback: string }) =>
    request<{ plan: Record<string, unknown> }>(
      `/pipeline/${pid}/generate-revision-plan`, { method: "POST", body }),
  approveRevisionPlan: (pid: string, body: { approved: boolean; adjustments?: string }) =>
    request<{ status: string; plan?: Record<string, unknown>; adjustments?: string }>(
      `/pipeline/${pid}/approve-revision-plan`, { method: "POST", body }),
  getRevisionPlan: (pid: string) =>
    request<{ plan: Record<string, unknown> | null; approved: boolean }>(
      `/pipeline/${pid}/revision-plan`),
  resetRun: (pid: string, body?: { phase?: string; chapter?: number; max_chapter_retries?: number; max_editorial_lock_retries?: number }) =>
    request<{ reset: boolean; mode: string; current_phase?: string; max_chapter_retries?: number; max_editorial_lock_retries?: number; editorial_lock_retries?: number }>(
      `/pipeline/${pid}/reset-run`, { method: "POST", body: body ?? {} }),
  updateInstructions: (pid: string, instructions: string) =>
    request<Record<string, unknown>>(`/pipeline/${pid}/update-instructions`, {
      method: "POST", body: { instructions } }),
  // ── Server-side auto-run ───────────────────────────────────────────────
  startAutoRun: (pid: string, instructions: string) =>
    request<{ started: boolean; reason?: string }>(
      `/pipeline/${pid}/auto-run/start`, { method: "POST", body: { instructions } }),
  stopAutoRun: (pid: string) =>
    request<{ stopped: boolean }>(
      `/pipeline/${pid}/auto-run/stop`, { method: "POST", body: {} }),
  autoRunStatus: (pid: string) =>
    request<{
      running: boolean;
      countdown: number;
      failed: boolean;
      failed_phase: string;
      log: { time: string; message: string; type: "info" | "warn" | "error" }[];
    }>(`/pipeline/${pid}/auto-run/status`),
  outputs: (pid: string) => request<Record<string, unknown>>(`/pipeline/${pid}/outputs`),
  outputFile: (pid: string, path: string) =>
    request<{ content: string; exists: boolean }>(
      `/pipeline/${pid}/output-file?path=${encodeURIComponent(path)}`),
  // Download the project's files as a ZIP. The export endpoint requires the
  // JWT auth header, so a plain <a download> won't work — we fetch the blob
  // with the header and trigger a client-side download.
  exportProject: async (pid: string): Promise<void> => {
    const token = getToken();
    const res = await fetch(`${API_BASE}/pipeline/${pid}/export`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      if (res.status === 401) {
        setToken(null);
        if (!location.pathname.startsWith("/studio/login")) location.href = "/studio/login";
      }
      const text = await res.text().catch(() => "");
      throw new ApiError(res.status, text.slice(0, 200) || res.statusText);
    }
    // Derive filename from the Content-Disposition header when present.
    const cd = res.headers.get("Content-Disposition") || "";
    const match = /filename="?([^"]+)"?/.exec(cd);
    const filename = match ? match[1] : "project_export.zip";
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  // Recover project content from version history (for when on-disk files are lost).
  exportFromVersions: async (pid: string): Promise<void> => {
    const token = getToken();
    const res = await fetch(`${API_BASE}/pipeline/${pid}/export-from-versions`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      if (res.status === 401) {
        setToken(null);
        if (!location.pathname.startsWith("/studio/login")) location.href = "/studio/login";
      }
      const text = await res.text().catch(() => "");
      throw new ApiError(res.status, text.slice(0, 200) || res.statusText);
    }
    const cd = res.headers.get("Content-Disposition") || "";
    const match = /filename="?([^"]+)"?/.exec(cd);
    const filename = match ? match[1] : "version_history.zip";
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  pipelineChat: (pid: string, body: {
    messages: { role: string; content: string }[];
    context_artifact?: string; context_chapter?: number;
  }) => request<{ reply: string; suggested_instructions: string | null; model_used: string }>(
    `/pipeline/${pid}/chat`, { method: "POST", body }),

  // Versions
  listVersions: (pid: string) =>
    request<{ groups: VersionGroup[]; total: number }>(`/versions/${pid}`),
  versionHistory: (pid: string, ct: string, chapter: string) =>
    request<{ versions: VersionSummary[] }>(`/versions/${pid}/history/${ct}/${chapter}`),
  versionDetail: (vid: string) => request<VersionDetail>(`/versions/detail/${vid}`),
  restoreVersion: (pid: string, vid: string) =>
    request<{ restored: boolean; path: string; word_count: number; new_version_id: string }>(
      `/versions/${pid}/restore/${vid}`, { method: "POST" }),
  versionDiff: (vidA: string, vidB: string) =>
    request<{
      version_a_id: string; version_b_id: string; content_type: string;
      chapter_number: number | null;
      lines: { type: string; old_line?: string; new_line?: string }[];
      stats: { insertions: number; deletions: number; unchanged: number };
    }>(`/versions/diff/${vidA}/${vidB}`),

  // Help
  helpChat: (body: {
    messages: { role: string; content: string }[];
  }) => request<{ reply: string; model_used: string }>(
    `/help/chat`, { method: "POST", body }),

  // Writing
  listChapters: (pid: string) => request<{ chapters: Chapter[] }>(`/writing/${pid}/chapters`),
  readChapter: (pid: string, path: string) =>
    request<{ path: string; content: string; word_count: number }>(
      `/writing/${pid}/chapter?path=${encodeURIComponent(path)}`),
  saveChapter: (pid: string, body: {
    path?: string; filename?: string; content: string; chapter_number?: number;
  }) => request<{ saved: boolean; path: string; word_count: number }>(
    `/writing/${pid}/chapter`, { method: "POST", body }),
  listProfiles: (pid: string) =>
    request<{ profiles: { path: string; name: string; content: string }[] }>(
      `/writing/${pid}/profiles`),
  writingChat: (pid: string, body: {
    messages: { role: string; content: string }[]; chapter_content?: string;
  }) => request<{ reply: string; model_used: string }>(
    `/writing/${pid}/chat`, { method: "POST", body }),

  // Editorial Review
  listCritics: () =>
    request<{ critics: { id: string; label: string; description: string; category: string }[] }>(
      "/editorial/critics"),
  // Review CRUD
  createEditorialReview: (body: { title?: string; content: string; format?: string }) =>
    request<{ id: string; title: string; format: string; created_at: string }>(
      "/editorial/reviews", { method: "POST", body }),
  listEditorialReviews: () =>
    request<{ reviews: { id: string; title: string; format: string; created_at: string; updated_at: string }[] }>(
      "/editorial/reviews"),
  getEditorialReview: (id: string) =>
    request<{
      id: string; title: string; format: string;
      original_content: string; current_content: string;
      supporting_materials: Record<string, string>;
      reports: { report_type: string; report: string; verdict: string; created_at: string }[];
      versions: { version_number: number; feedback: string; instructions: string; created_at: string }[];
      created_at: string; updated_at: string;
    }>(`/editorial/reviews/${id}`),
  updateEditorialReview: (id: string, body: { title?: string; content?: string; format?: string }) =>
    request<{ updated: boolean }>(`/editorial/reviews/${id}`, { method: "PUT", body }),
  deleteEditorialReview: (id: string) =>
    request<{ deleted: boolean }>(`/editorial/reviews/${id}`, { method: "DELETE" }),
  // Run critics/readers on a saved review
  runEditorialCritics: (id: string, body: { critics?: string[] }) =>
    request<{ results: Record<string, { report: string; verdict: string }>; model_used: string }>(
      `/editorial/reviews/${id}/review`, { method: "POST", body }),
  runEditorialReader: (id: string, body: { reader_type: string }) =>
    request<{ report: string; verdict: string; model_used: string }>(
      `/editorial/reviews/${id}/reader`, { method: "POST", body }),
  // Generate supporting materials
  generateEditorialMaterials: (id: string, body: { material_types?: string[] }) =>
    request<{ materials: Record<string, string>; model_used: string }>(
      `/editorial/reviews/${id}/materials`, { method: "POST", body }),
  // Revision with version tracking
  editorialRevise: (id: string, body: { instructions?: string; rounds?: number }) =>
    request<{ revised_content: string; model_used: string; version_number: number }>(
      `/editorial/reviews/${id}/revise`, { method: "POST", body }),
  // Version history
  getEditorialVersions: (id: string) =>
    request<{ versions: { version_number: number; content: string; feedback: string; instructions: string; created_at: string }[] }>(
      `/editorial/reviews/${id}/versions`),

  // Custom personas
  listPersonas: () =>
    request<{ personas: { id: string; persona_id: string; name: string; one_line: string; severity: number; is_builtin: boolean; created_at: string | null }[] }>(
      "/editorial/personas"),
  getPersona: (id: string) =>
    request<{ persona: Record<string, unknown>; is_builtin: boolean; db_id?: string }>(
      `/editorial/personas/${id}`),
  savePersona: (body: { persona: Record<string, unknown> }) =>
    request<{ id: string; persona_id: string; name: string }>(
      "/editorial/personas", { method: "POST", body }),
  updatePersona: (id: string, body: { persona: Record<string, unknown> }) =>
    request<{ updated: boolean; persona_id: string; name: string }>(
      `/editorial/personas/${id}`, { method: "PUT", body }),
  deletePersona: (id: string) =>
    request<{ deleted: boolean }>(
      `/editorial/personas/${id}`, { method: "DELETE" }),
  importPersona: (body: Record<string, unknown>) =>
    request<{ id: string; name: string }>(
      "/editorial/personas/import", { method: "POST", body }),
  compilePersona: (body: { description: string; genre?: string; audience?: string; draft_stage?: string; rubric?: Record<string, unknown> | null }) =>
    request<{ persona: Record<string, unknown> | null; warnings: string[]; error: string | null; raw_response: string }>(
      "/editorial/compile-persona", { method: "POST", body }),
  // Run custom persona on a review
  runPersona: (reviewId: string, body: { persona_id: string; rubric?: Record<string, unknown> | null; severity?: number }) =>
    request<{ run_id: string; persona_name: string; output: string; severity: number; model_used: string }>(
      `/editorial/reviews/${reviewId}/run-persona`, { method: "POST", body }),
  warmCache: (reviewId: string) =>
    request<{ warmed: boolean }>(
      `/editorial/reviews/${reviewId}/warm-cache`, { method: "POST" }),
  listRuns: (reviewId: string) =>
    request<{ runs: { id: string; persona_name: string; severity: number; output: string; cache_hit_tokens: number; cache_miss_tokens: number; cost_usd: number; created_at: string }[] }>(
      `/editorial/reviews/${reviewId}/runs`),
  // Argument Reader v2 batch
  runArgumentReader: (reviewId: string, body?: { include_amplification?: boolean }) =>
    request<{ readers: { persona_id: string; name: string; output: string }[]; synthesis: { output: string; name: string } | null; amplification: { output: string; name: string } | null; model_used: string }>(
      `/editorial/reviews/${reviewId}/run-argument-reader`, { method: "POST", body: body || {} }),
  // Decompose: one description → multiple readers + synthesis
  decomposePersona: (body: { description: string; genre?: string; audience?: string; draft_stage?: string; rubric?: Record<string, unknown> | null }) =>
    request<{ decomposition_name: string; decomposition_rationale: string; readers: Record<string, unknown>[]; synthesis_focus: string }>(
      "/editorial/decompose-persona", { method: "POST", body }),
  decomposeAndRun: (reviewId: string, body: { description: string; genre?: string; audience?: string; draft_stage?: string; rubric?: Record<string, unknown> | null; include_amplification?: boolean }) =>
    request<{ decomposition_name: string; decomposition_rationale: string; readers: { persona_id: string; name: string; output: string }[]; synthesis: { output: string; name: string } | null; amplification: { output: string; name: string } | null; model_used: string }>(
      `/editorial/reviews/${reviewId}/decompose-and-run`, { method: "POST", body }),

  // ── File browser (Studio editor) ───────────────────────────────────────
  fileTree: (pid: string) =>
    request<{ root: string; tree: FileNode[] }>(`/files/${pid}/tree`),
  readFile: (pid: string, path: string) =>
    request<{ path: string; content: string | null; kind: string; size: number; word_count?: number }>(
      `/files/${pid}/read?path=${encodeURIComponent(path)}`),
  saveFile: (pid: string, body: { path: string; content: string }) =>
    request<{ saved: boolean; path: string; size: number; word_count: number }>(
      `/files/${pid}/save`, { method: "POST", body }),
  createFileItem: (pid: string, body: { path: string; is_directory?: boolean; content?: string }) =>
    request<{ created: boolean; path: string; is_directory: boolean }>(
      `/files/${pid}/create`, { method: "POST", body }),
  deleteFileItem: (pid: string, path: string) =>
    request<{ deleted: boolean; path: string }>(
      `/files/${pid}/delete?path=${encodeURIComponent(path)}`, { method: "DELETE" }),
  renameFileItem: (pid: string, body: { old_path: string; new_path: string }) =>
    request<{ renamed: boolean; old_path: string; new_path: string }>(
      `/files/${pid}/rename`, { method: "POST", body }),

  // Admin
  listApprovedEmails: () =>
    request<{ email: string; is_admin: boolean; added_by: string | null; created_at: string | null }[]>(
      "/admin/approved-emails"),
  addApprovedEmail: (email: string, is_admin: boolean = false) =>
    request<{ email: string; is_admin: boolean }>(
      "/admin/approved-emails", { method: "POST", body: { email, is_admin } }),
  removeApprovedEmail: (email: string) =>
    request<{ deleted: string }>(
      `/admin/approved-emails/${encodeURIComponent(email)}`, { method: "DELETE" }),

  // Audiobook pipeline
  get: (path: string) => request<unknown>(path),
  post: (path: string, body?: unknown) => request<unknown>(path, { method: "POST", body }),
};
