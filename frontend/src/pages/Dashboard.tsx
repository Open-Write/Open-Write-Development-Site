import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Project } from "../api";
import Layout from "../components/Layout";

const FORMATS = [
  { value: "novel", label: "Novel" },
  { value: "screenplay", label: "Screenplay" },
  { value: "tv", label: "TV Script" },
];

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [format, setFormat] = useState("novel");
  const [creating, setCreating] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .listProjects()
      .then(setProjects)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setError("");
    try {
      await api.createProject(name.trim(), description.trim(), format);
      setName(""); setDescription(""); setFormat("novel"); setShowNew(false);
      load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const remove = async (p: Project) => {
    if (!confirm(`Delete "${p.name}"? This removes all its data and versions.`)) return;
    try {
      await api.deleteProject(p.id);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <Layout>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-100">Projects</h1>
        <button className="btn-primary" onClick={() => setShowNew((v) => !v)}>
          {showNew ? "Cancel" : "New project"}
        </button>
      </div>

      {error && <div className="mb-4 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      {showNew && (
        <form onSubmit={create} className="card mb-6 space-y-4 p-5">
          <div>
            <label className="mb-1 block text-xs text-gray-400">Project name</label>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-400">Description (optional)</label>
            <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-400">Format</label>
            <select className="input" value={format} onChange={(e) => setFormat(e.target.value)}>
              {FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
          </div>
          <button className="btn-primary" disabled={creating || !name.trim()}>
            {creating ? "Creating…" : "Create project"}
          </button>
        </form>
      )}

      {loading ? (
        <div className="text-gray-500">Loading…</div>
      ) : projects.length === 0 ? (
        <div className="card p-10 text-center text-gray-500">
          No projects yet. Create your first project to start the writing pipeline.
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <div key={p.id} className="card group relative flex flex-col p-5 transition-colors hover:border-accent-soft/50">
              <Link to={`/projects/${p.id}`} className="flex-1">
                <div className="mb-2 flex items-center gap-2">
                  <span className="badge bg-ink-800 text-gray-400 capitalize">{p.format}</span>
                </div>
                <h3 className="mb-1 text-lg font-semibold text-gray-100">{p.name}</h3>
                <p className="line-clamp-2 text-sm text-gray-500">{p.description || "No description"}</p>
              </Link>
              <div className="mt-4 flex items-center justify-between">
                <span className="text-xs text-gray-600">
                  {p.updated_at ? new Date(p.updated_at).toLocaleDateString() : ""}
                </span>
                <button
                  className="text-xs text-gray-500 opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                  onClick={() => remove(p)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Layout>
  );
}
