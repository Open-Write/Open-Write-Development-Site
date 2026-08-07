import { useEffect, useState } from "react";
import { api, type Provider, type ProvidersConfig } from "../api";
import Layout from "../components/Layout";

export default function Settings() {
  const [cfg, setCfg] = useState<ProvidersConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  // Live model lists fetched from each provider's /models endpoint.
  // Key = provider_id. Value = array of {id, name} fetched live (or curated fallback).
  const [liveModels, setLiveModels] = useState<Record<string, { id: string; name: string }[]>>({});
  const [fetchingModels, setFetchingModels] = useState<Record<string, boolean>>({});
  const [modelSource, setModelSource] = useState<Record<string, string>>({}); // "live"|"curated"

  const load = () => {
    setLoading(true);
    api
      .getProviders()
      .then(setCfg)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const updateProvider = (id: string, patch: Partial<Provider>) => {
    if (!cfg) return;
    setCfg({ ...cfg, providers: cfg.providers.map((p) => (p.id === id ? { ...p, ...patch } : p)) });
  };

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError("");
    setStatus("");
    try {
      const saved = await api.updateProviders({
        providers: cfg.providers,
        default_model: cfg.default_model,
        writer_model: cfg.writer_model,
        critic_model: cfg.critic_model,
        planner_model: cfg.planner_model,
      });
      setCfg(saved);
      setStatus("Settings saved.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const test = async (id: string) => {
    setTesting(id);
    setTestResult((r) => ({ ...r, [id]: "" }));
    // Save first so the backend tests the current key.
    try {
      await save();
      const r = await api.testConnection(id);
      setTestResult((res) => ({
        ...res,
        [id]: r.ok ? `OK · ${r.model_count ?? "?"} models` : `Failed: ${r.error || "unknown"}`,
      }));
      if (r.ok) fetchModels(id);
    } catch (e) {
      setTestResult((res) => ({ ...res, [id]: `Failed: ${(e as Error).message}` }));
    } finally {
      setTesting(null);
    }
  };

  const fetchModels = async (id: string) => {
    setFetchingModels((f) => ({ ...f, [id]: true }));
    try {
      const r = await api.listProviderModels(id);
      setLiveModels((m) => ({ ...m, [id]: r.models }));
      setModelSource((s) => ({ ...s, [id]: r.source }));
    } catch {
      // silently ignore — user will see no live models, curated list remains
    } finally {
      setFetchingModels((f) => ({ ...f, [id]: false }));
    }
  };

  if (loading) return <Layout><div className="text-gray-500">Loading settings…</div></Layout>;
  if (!cfg) return <Layout><div className="text-red-300">{error || "Failed to load settings."}</div></Layout>;

  // Build combined model options from live-fetched lists (preferred) or provider seed lists.
  const allModelOptions = cfg.providers.flatMap((p) => {
    const models = liveModels[p.id] ?? p.models.map((m) => ({ id: `${p.id}/${m}`, name: m }));
    return models.map((m) => ({ value: m.id, label: `${p.label} — ${m.name}` }));
  });

  return (
    <Layout>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-100">Settings</h1>
          <p className="text-sm text-gray-500">Configure LLM providers and model routing. Keys are stored per account.</p>
        </div>
        <div className="flex items-center gap-3">
          {status && <span className="text-sm text-emerald-400">{status}</span>}
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
        </div>
      </div>

      {error && <div className="mb-4 rounded-lg bg-red-600/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <div className="mb-6 rounded-lg border border-accent-soft/30 bg-accent-soft/5 px-4 py-3 text-sm text-gray-300">
        <strong className="text-gray-100">Quick start:</strong> Paste your OpenRouter API key below and set a default
        model (e.g. <code className="text-accent">openai/gpt-4o-mini</code>), then Save. That's enough to run the pipeline.
      </div>

      <div className="space-y-4">
        {cfg.providers.map((p) => (
          <div key={p.id} className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <h3 className="text-base font-semibold text-gray-100">{p.label}</h3>
              <span className="badge bg-ink-800 text-gray-500">{p.id}</span>
              {p.api_key && <span className="badge bg-emerald-600/15 text-emerald-300">configured</span>}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs text-gray-400">API key</label>
                <input
                  className="input" type="password" placeholder="sk-…"
                  value={p.api_key || ""}
                  onChange={(e) => updateProvider(p.id, { api_key: e.target.value })}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-gray-400">Base URL</label>
                <input
                  className="input"
                  value={p.base_url || ""}
                  onChange={(e) => updateProvider(p.id, { base_url: e.target.value })}
                />
              </div>
            </div>
            {p.id === "zai" && (
              <div className="mt-3 space-y-2">
                <p className="rounded-lg border border-accent-soft/30 bg-accent-soft/5 px-3 py-2 text-xs text-gray-400">
                  GLM models on the Coding Plan endpoint (Singapore). Use a separate Xiaomi MiMo key in the MiMo provider below.
                </p>
              </div>
            )}
            {p.id === "mimo" && (
              <div className="mt-3 space-y-2">
                <p className="rounded-lg border border-accent-soft/30 bg-accent-soft/5 px-3 py-2 text-xs text-gray-400">
                  Same Singapore endpoint as Z.AI Coding Plan. Paste your MiMo API key here.
                </p>
              </div>
            )}
            <div className="mt-3 flex items-center gap-3">
              <button className="btn-ghost !py-1.5 text-xs" onClick={() => test(p.id)} disabled={testing === p.id}>
                {testing === p.id ? "Testing…" : "Test connection"}
              </button>
              <button
                className="btn-ghost !py-1.5 text-xs"
                onClick={() => fetchModels(p.id)}
                disabled={!p.api_key || fetchingModels[p.id]}
              >
                {fetchingModels[p.id] ? "Fetching…" : "Fetch models"}
              </button>
              {liveModels[p.id] && (
                <span className="text-xs text-gray-500">
                  {liveModels[p.id].length} models
                  {modelSource[p.id] === "live" ? " (live)" : " (curated)"}
                </span>
              )}
              {testResult[p.id] && (
                <span className={`text-xs ${testResult[p.id].startsWith("OK") ? "text-emerald-400" : "text-red-300"}`}>
                  {testResult[p.id]}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="card mt-6 p-5">
        <h3 className="mb-3 text-base font-semibold text-gray-100">Model routing</h3>
        <p className="mb-4 text-sm text-gray-500">
          Assign models to each pipeline role. Values are fully-qualified as
          <code className="text-accent"> provider/model</code>. Leave writer/critic/planner
          unset to fall back to the default model.
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          {([
            ["default_model", "Default model"],
            ["writer_model", "Writer model"],
            ["critic_model", "Critic model"],
            ["planner_model", "Planner model"],
          ] as const).map(([key, label]) => (
            <div key={key}>
              <label className="mb-1 block text-xs text-gray-400">{label}</label>
              <select
                className="input appearance-none bg-ink-850"
                value={(cfg[key] as string) || ""}
                onChange={(e) => setCfg({ ...cfg, [key]: e.target.value })}
              >
                <option value="">{key === "default_model" ? "— select a model —" : "(uses default model)"}</option>
                {allModelOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-gray-500">
          Click "Fetch models" on a provider card to load its live model list. Models appear here grouped by provider. Providers without a key show their curated list only.
        </p>
      </div>
    </Layout>
  );
}
