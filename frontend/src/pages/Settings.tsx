import { useEffect, useState } from "react";
import { api, type Provider, type ProvidersConfig } from "../api";
import Layout from "../components/Layout";

// ── Account tier badge ──────────────────────────────────────────────────────

function TierBadge({ tier }: { tier: string }) {
  const colors: Record<string, string> = {
    basic: "bg-ink-800 text-gray-400",
    pro: "bg-accent/20 text-accent",
    admin: "bg-emerald-600/15 text-emerald-300",
  };
  return (
    <span className={`badge ${colors[tier] || colors.basic}`}>
      {tier.charAt(0).toUpperCase() + tier.slice(1)}
    </span>
  );
}

// ── Provider selector data ──────────────────────────────────────────────────

const PROVIDER_OPTIONS = [
  { id: "deepseek", label: "DeepSeek", description: "Chinese AI lab — cheapest cache-hit rates", accountTypes: ["pay-as-you-go"] },
  { id: "mimo", label: "Xiaomi MiMo", description: "Hybrid SWA architecture — same pricing as DeepSeek Flash", accountTypes: ["pay-as-you-go", "token-plan"] },
  { id: "openrouter", label: "OpenRouter", description: "Multi-provider gateway — access 100+ models", accountTypes: ["pay-as-you-go"] },
  { id: "openai", label: "OpenAI", description: "GPT-4o, o4-mini, etc.", accountTypes: ["pay-as-you-go", "subscription"] },
  { id: "anthropic", label: "Anthropic", description: "Claude Sonnet, Haiku, Opus", accountTypes: ["pay-as-you-go", "subscription"] },
  { id: "google", label: "Google AI", description: "Gemini 2.5 Flash/Pro", accountTypes: ["pay-as-you-go"] },
  { id: "glm", label: "GLM / Z.AI", description: "Chinese AI — free tier available", accountTypes: ["pay-as-you-go", "free-tier"] },
  { id: "zai", label: "Z.AI Coding Plan", description: "GLM models via Singapore endpoint", accountTypes: ["subscription"] },
  { id: "mistral", label: "Mistral", description: "Mistral Large/Small", accountTypes: ["pay-as-you-go"] },
  { id: "groq", label: "Groq", description: "Ultra-fast Llama inference", accountTypes: ["pay-as-you-go"] },
  { id: "xai", label: "xAI", description: "Grok-3", accountTypes: ["pay-as-you-go"] },
];

// ── Main Settings Page ──────────────────────────────────────────────────────

export default function Settings() {
  const [cfg, setCfg] = useState<ProvidersConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [liveModels, setLiveModels] = useState<Record<string, { id: string; name: string }[]>>({});
  const [fetchingModels, setFetchingModels] = useState<Record<string, boolean>>({});

  // Token usage state
  const [tokenUsage, setTokenUsage] = useState<{
    tokens_used: number; tokens_remaining: number; monthly_allowance: number;
    reset_date: string | null; tier: string; allowed_models: string[] | null;
  } | null>(null);

  // Provider selector state
  const [selectedProvider, setSelectedProvider] = useState("");
  const [selectedAccountType, setSelectedAccountType] = useState("");
  const [newApiKey, setNewApiKey] = useState("");

  // A/B reader state
  const [abReaderEnabled, setAbReaderEnabled] = useState(false);
  const [abReaderModel, setAbReaderModel] = useState("");

  const load = () => {
    setLoading(true);
    Promise.all([
      api.getProviders(),
      api.getTokenUsage().catch(() => null),
    ]).then(([providers, usage]) => {
      setCfg(providers);
      setTokenUsage(usage);
      // Load A/B reader state from settings
      const routing = providers.model_routing || {};
      setAbReaderEnabled(!!routing.ab_reader_enabled);
      setAbReaderModel(routing.ab_reader_model || "");
    })
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
    setError(""); setStatus("");
    try {
      const routing = {
        ...(cfg.model_routing || {}),
        ab_reader_enabled: abReaderEnabled,
        ab_reader_model: abReaderModel,
      };
      const saved = await api.updateProviders({
        providers: cfg.providers,
        default_model: cfg.default_model,
        writer_model: cfg.writer_model,
        critic_model: cfg.critic_model,
        planner_model: cfg.planner_model,
        audiobook_model: cfg.audiobook_model,
        model_routing: routing,
      });
      setCfg(saved);
      setStatus("Saved.");
    } catch (e) { setError((e as Error).message); }
    finally { setSaving(false); }
  };

  const test = async (id: string) => {
    setTesting(id); setTestResult((r) => ({ ...r, [id]: "" }));
    try {
      await save();
      const r = await api.testConnection(id);
      setTestResult((res) => ({ ...res, [id]: r.ok ? `OK · ${r.model_count ?? "?"} models` : `Failed: ${r.error || "unknown"}` }));
      if (r.ok) fetchModels(id);
    } catch (e) { setTestResult((res) => ({ ...res, [id]: `Failed: ${(e as Error).message}` })); }
    finally { setTesting(null); }
  };

  const fetchModels = async (id: string) => {
    setFetchingModels((f) => ({ ...f, [id]: true }));
    try {
      const r = await api.listProviderModels(id);
      setLiveModels((m) => ({ ...m, [id]: r.models }));
    } catch { /* silent */ }
    finally { setFetchingModels((f) => ({ ...f, [id]: false })); }
  };

  const addProvider = () => {
    if (!cfg || !selectedProvider) return;
    const seed = PROVIDER_OPTIONS.find((p) => p.id === selectedProvider);
    if (!seed) return;
    // Check if provider already exists
    if (cfg.providers.find((p) => p.id === selectedProvider)) {
      setError(`${seed.label} is already configured.`);
      return;
    }
    const newProvider: Provider = {
      id: seed.id,
      label: seed.label,
      base_url: "",
      api_key: newApiKey,
      models: [],
    };
    setCfg({ ...cfg, providers: [...cfg.providers, newProvider] });
    setSelectedProvider("");
    setSelectedAccountType("");
    setNewApiKey("");
    setError("");
  };

  const removeProvider = (id: string) => {
    if (!cfg) return;
    setCfg({ ...cfg, providers: cfg.providers.filter((p) => p.id !== id) });
  };

  // Build model options for routing dropdowns
  const allModelOptions = cfg?.providers.flatMap((p) => {
    const models = liveModels[p.id] ?? p.models.map((m) => ({ id: `${p.id}/${m}`, name: m }));
    return models.map((m) => ({ value: m.id, label: `${p.label} — ${m.name}` }));
  }) ?? [];

  // Filter models by tier
  const allowedModels = tokenUsage?.allowed_models;
  const filteredModelOptions = allowedModels
    ? allModelOptions.filter((opt) => allowedModels.some((a) => opt.value === a || opt.value.startsWith(a.split("/")[0] + "/")))
    : allModelOptions;

  if (loading) return <Layout><div className="text-gray-500">Loading settings…</div></Layout>;
  if (!cfg) return <Layout><div className="text-red-300">{error || "Failed to load settings."}</div></Layout>;

  const usedPct = tokenUsage
    ? Math.round((tokenUsage.tokens_used / tokenUsage.monthly_allowance) * 100)
    : 0;

  return (
    <Layout>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-100">Settings</h1>
          <p className="text-sm text-gray-500">Manage your account, providers, and model routing.</p>
        </div>
        <div className="flex items-center gap-3">
          {status && <span className="text-sm text-emerald-400">{status}</span>}
          {error && <span className="text-sm text-red-300">{error}</span>}
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
        </div>
      </div>

      {/* ── Section 1: Token Usage ─────────────────────────────────────────── */}
      <div className="card mb-4 p-5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-100">Token Allowance</h3>
          <TierBadge tier={tokenUsage?.tier || "basic"} />
        </div>

        {tokenUsage && (
          <>
            <div className="mb-3">
              <div className="mb-1 flex items-center justify-between text-sm">
                <span className="text-gray-400">
                  {tokenUsage.tokens_used.toLocaleString()} / {tokenUsage.monthly_allowance.toLocaleString()} tokens used
                </span>
                <span className="text-gray-500">{usedPct}%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-ink-800">
                <div
                  className={`h-full rounded-full transition-all ${usedPct > 90 ? "bg-red-500" : usedPct > 70 ? "bg-amber-500" : "bg-accent"}`}
                  style={{ width: `${Math.min(usedPct, 100)}%` }}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500">Remaining</span>
                <p className="font-medium text-gray-200">{tokenUsage.tokens_remaining.toLocaleString()} tokens</p>
              </div>
              <div>
                <span className="text-gray-500">Resets</span>
                <p className="font-medium text-gray-200">
                  {tokenUsage.reset_date
                    ? new Date(tokenUsage.reset_date).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })
                    : "On next use"}
                </p>
              </div>
            </div>
            {tokenUsage.tier === "basic" && (
              <p className="mt-3 text-xs text-gray-500">
                Basic tier: access to DeepSeek-V4-Flash and MiMo-V2.5. Upgrade to Pro for all models.
              </p>
            )}
          </>
        )}
      </div>

      {/* ── Section 2: Buy More Tokens ─────────────────────────────────────── */}
      <div className="card mb-4 p-5">
        <h3 className="mb-2 text-base font-semibold text-gray-100">Need More Tokens?</h3>
        <p className="text-sm text-gray-500">
          Additional token packs will be available for purchase soon.
          Your current allowance refreshes automatically each billing cycle.
        </p>
        <button className="btn-ghost mt-3 !py-1.5 text-xs" disabled>
          Buy tokens — coming soon
        </button>
      </div>

      {/* ── Section 3: Provider Connections ─────────────────────────────────── */}
      <div className="card mb-4 p-5">
        <h3 className="mb-2 text-base font-semibold text-gray-100">Providers &amp; Default Model</h3>
        <p className="mb-4 text-sm text-gray-500">
          Choose your default model for generation using Open-Write tokens, then connect
          your own provider keys below to unlock additional models.
        </p>

        {/* Default model for Open-Write token generation */}
        <div className="mb-4 rounded-lg border border-accent-soft/30 bg-accent-soft/5 p-4">
          <label className="mb-2 block text-sm font-medium text-gray-200">Default model for generation</label>
          <p className="mb-2 text-xs text-gray-500">
            This model is used when generating with your Open-Write token allowance.
          </p>
          <select className="input appearance-none bg-ink-850"
            value={cfg.default_model || ""}
            onChange={(e) => setCfg({ ...cfg, default_model: e.target.value })}>
            <option value="">— select —</option>
            {filteredModelOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {/* Add provider selector */}
        <div className="mb-4 rounded-lg border border-edge p-4 space-y-3">
          <h4 className="text-sm font-medium text-gray-300">Add a provider</h4>
          <div className="grid gap-3 md:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs text-gray-400">Provider</label>
              <select className="input appearance-none bg-ink-850" value={selectedProvider}
                onChange={(e) => { setSelectedProvider(e.target.value); setSelectedAccountType(""); }}>
                <option value="">— select —</option>
                {PROVIDER_OPTIONS.filter((p) => !cfg.providers.find((c) => c.id === p.id)).map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </div>
            {selectedProvider && (
              <div>
                <label className="mb-1 block text-xs text-gray-400">Account type</label>
                <select className="input appearance-none bg-ink-850" value={selectedAccountType}
                  onChange={(e) => setSelectedAccountType(e.target.value)}>
                  <option value="">— select —</option>
                  {PROVIDER_OPTIONS.find((p) => p.id === selectedProvider)?.accountTypes.map((t) => (
                    <option key={t} value={t}>{t === "pay-as-you-go" ? "Pay-as-you-go" : t === "token-plan" ? "Token Plan" : t === "subscription" ? "Subscription" : "Free tier"}</option>
                  ))}
                </select>
              </div>
            )}
            {selectedProvider && selectedAccountType && (
              <div>
                <label className="mb-1 block text-xs text-gray-400">API key</label>
                <input className="input" type="password" placeholder="sk-…" value={newApiKey}
                  onChange={(e) => setNewApiKey(e.target.value)} />
              </div>
            )}
          </div>
          {selectedProvider && selectedAccountType && (
            <div className="rounded-lg border border-emerald-600/20 bg-emerald-600/5 px-3 py-2 text-xs text-emerald-300">
              Your API key is encrypted at rest using AES-256. It is never stored in plaintext,
              never logged, and cannot be retrieved by anyone — including our team.
              You can delete it at any time. The key is only decrypted in memory
              at the moment it is used to make an API call on your behalf.
            </div>
          )}
          <button className="btn-ghost !py-1.5 text-xs" onClick={addProvider}
            disabled={!selectedProvider || !selectedAccountType}>
            Add provider
          </button>
        </div>

        {/* Configured providers */}
        <div className="space-y-3">
          {cfg.providers.map((p) => {
            const hasServerKey = (cfg.server_key_providers || []).includes(p.id);
            const isUsingOwnKey = p.key_source === "user";
            return (
            <div key={p.id} className="rounded-lg border border-edge p-4">
              <div className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h4 className="text-sm font-semibold text-gray-200">{p.label}</h4>
                  {p.key_source === "openwrite" && (
                    <span className="badge bg-accent/20 text-accent">Open-Write key</span>
                  )}
                  {p.key_source === "user" && (
                    <span className="badge bg-emerald-600/15 text-emerald-300">Your key</span>
                  )}
                  {p.key_source === "none" && (
                    <span className="badge bg-ink-800 text-gray-500">no key</span>
                  )}
                </div>
                <button className="text-xs text-red-400 hover:text-red-300" onClick={() => removeProvider(p.id)}>
                  Remove
                </button>
              </div>

              {/* Key source toggle for providers with server keys */}
              {hasServerKey && (
                <div className="mb-3 flex items-center gap-4 text-xs">
                  <button
                    className={`rounded px-3 py-1.5 ${!isUsingOwnKey ? "bg-accent/20 text-accent" : "bg-ink-800 text-gray-500"}`}
                    onClick={() => updateProvider(p.id, { api_key: "" })}>
                    Use Open-Write key
                  </button>
                  <button
                    className={`rounded px-3 py-1.5 ${isUsingOwnKey ? "bg-emerald-600/15 text-emerald-300" : "bg-ink-800 text-gray-500"}`}
                    onClick={() => updateProvider(p.id, { api_key: p.api_key || "" })}>
                    Use your own key
                  </button>
                </div>
              )}

              {/* API key input — only show when using own key or no server key */}
              {(isUsingOwnKey || !hasServerKey) && (
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs text-gray-400">API key</label>
                    <input className="input" type="password" placeholder="sk-…"
                      value={p.api_key || ""}
                      onChange={(e) => updateProvider(p.id, { api_key: e.target.value })} />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-gray-400">Base URL</label>
                    <input className="input" value={p.base_url || ""}
                      onChange={(e) => updateProvider(p.id, { base_url: e.target.value })} />
                  </div>
                </div>
              )}

              {/* Info when using Open-Write key */}
              {!isUsingOwnKey && hasServerKey && (
                <p className="text-xs text-gray-500">
                  Using Open-Write's {p.label} key. Tokens are deducted from your monthly allowance.
                  {p.key_source === "openwrite" && " Switch to your own key to use your own account."}
                </p>
              )}

              <div className="mt-2 flex items-center gap-3">
                <button className="btn-ghost !py-1 text-xs" onClick={() => test(p.id)} disabled={testing === p.id}>
                  {testing === p.id ? "Testing…" : "Test"}
                </button>
                <button className="btn-ghost !py-1 text-xs" onClick={() => fetchModels(p.id)}
                  disabled={!p.api_key || fetchingModels[p.id]}>
                  {fetchingModels[p.id] ? "Fetching…" : "Fetch models"}
                </button>
                {liveModels[p.id] && <span className="text-xs text-gray-500">{liveModels[p.id].length} models</span>}
                {testResult[p.id] && (
                  <span className={`text-xs ${testResult[p.id].startsWith("OK") ? "text-emerald-400" : "text-red-300"}`}>
                    {testResult[p.id]}
                  </span>
                )}
              </div>
            </div>
            );
          })}
          {cfg.providers.length === 0 && (
            <p className="text-sm text-gray-500">No providers configured yet. Add one above.</p>
          )}
        </div>
      </div>

      {/* ── Section 4: Model Routing ───────────────────────────────────────── */}
      <div className="card mb-4 p-5">
        <h3 className="mb-2 text-base font-semibold text-gray-100">Model Routing</h3>
        <p className="mb-4 text-sm text-gray-500">
          Override the default model for specific pipeline phases.
          "Open-Write default" uses the model selected in the provider section above.
        </p>

        <div className="grid gap-3 md:grid-cols-2">
          {([
            ["writer_model", "Writer"],
            ["critic_model", "Critic / Editorial"],
            ["planner_model", "Planner (Architect)"],
          ] as const).map(([key, label]) => (
            <div key={key}>
              <label className="mb-1 block text-xs text-gray-400">{label}</label>
              <select className="input appearance-none bg-ink-850"
                value={(cfg[key] as string) || ""}
                onChange={(e) => setCfg({ ...cfg, [key]: e.target.value })}>
                <option value="">Open-Write default</option>
                {filteredModelOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
          ))}
        </div>

        {/* A/B Reader */}
        <div className="mt-4 rounded-lg border border-accent-soft/30 bg-accent-soft/5 p-4">
          <div className="mb-2 flex items-center gap-3">
            <input type="checkbox" id="ab-reader" checked={abReaderEnabled}
              onChange={(e) => setAbReaderEnabled(e.target.checked)} />
            <label htmlFor="ab-reader" className="text-sm font-medium text-gray-200">
              A/B Reader — run adversarial readers on two different models
            </label>
          </div>
          <p className="mb-3 text-xs text-gray-500">
            Recommended for quality assurance. Running the same reader prompt against two different
            models catches blind spots that a single model misses. The second model runs on every
            adversarial reader invocation alongside your primary critic model.
          </p>
          {abReaderEnabled && (
            <div>
              <label className="mb-1 block text-xs text-gray-400">Second reader model</label>
              <select className="input appearance-none bg-ink-850" value={abReaderModel}
                onChange={(e) => setAbReaderModel(e.target.value)}>
                <option value="">— select —</option>
                {filteredModelOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
          )}
        </div>

        {allowedModels && (
          <p className="mt-3 text-xs text-gray-500">
            Basic tier: only DeepSeek-V4-Flash and MiMo-V2.5 are available. Upgrade to Pro for all models.
          </p>
        )}
      </div>

      {/* ── Section 5: Audiobook ──────────────────────────────────────────── */}
      <div className="card mb-4 p-5">
        <h3 className="mb-2 text-base font-semibold text-gray-100">Audiobook</h3>
        <p className="mb-4 text-sm text-gray-500">
          Configure the model used for audiobook script generation. The audiobook pipeline
          converts your manuscript chapters into production scripts using an LLM, then
          synthesizes audio using MiMo TTS.
        </p>

        <div className="mb-4 rounded-lg border border-accent-soft/30 bg-accent-soft/5 p-4">
          <label className="mb-2 block text-sm font-medium text-gray-200">Script generation model</label>
          <p className="mb-2 text-xs text-gray-500">
            Only MiMo models are supported. This model converts manuscript text into
            audio script segments with narrator directions and dialogue attribution.
          </p>
          <select className="input appearance-none bg-ink-850"
            value={cfg.audiobook_model || ""}
            onChange={(e) => setCfg({ ...cfg, audiobook_model: e.target.value })}>
            <option value="">— use default model —</option>
            {filteredModelOptions
              .filter((opt) => opt.value.startsWith("mimo/"))
              .map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
          </select>
        </div>

        {/* MiMo key source info */}
        {(() => {
          const mimoProvider = cfg.providers.find((p) => p.id === "mimo");
          const hasServerKey = (cfg.server_key_providers || []).includes("mimo");
          if (!mimoProvider) {
            return (
              <p className="text-xs text-amber-400">
                MiMo provider is not configured. Add it in the Providers section above to use audiobook generation.
              </p>
            );
          }
          const keySource = mimoProvider.key_source;
          return (
            <div className="rounded-lg border border-edge p-4">
              <div className="flex items-center gap-2 mb-2">
                <h4 className="text-sm font-medium text-gray-200">MiMo TTS key source</h4>
                {keySource === "openwrite" && (
                  <span className="badge bg-accent/20 text-accent">Open-Write key</span>
                )}
                {keySource === "user" && (
                  <span className="badge bg-emerald-600/15 text-emerald-300">Your key</span>
                )}
                {keySource === "none" && (
                  <span className="badge bg-ink-800 text-gray-500">no key</span>
                )}
              </div>
              {hasServerKey && (
                <div className="mb-3 flex items-center gap-4 text-xs">
                  <button
                    className={`rounded px-3 py-1.5 ${keySource !== "user" ? "bg-accent/20 text-accent" : "bg-ink-800 text-gray-500"}`}
                    onClick={() => updateProvider("mimo", { api_key: "" })}>
                    Use Open-Write tokens
                  </button>
                  <button
                    className={`rounded px-3 py-1.5 ${keySource === "user" ? "bg-emerald-600/15 text-emerald-300" : "bg-ink-800 text-gray-500"}`}
                    onClick={() => updateProvider("mimo", { api_key: mimoProvider.api_key || "" })}>
                    Use your own MiMo subscription
                  </button>
                </div>
              )}
              <p className="text-xs text-gray-500">
                {keySource === "openwrite"
                  ? "Using Open-Write's MiMo key. Tokens are deducted from your monthly allowance."
                  : keySource === "user"
                  ? "Using your own MiMo API key. Usage is billed to your MiMo account."
                  : "No MiMo API key set. Add one in the Providers section or switch to Open-Write key."}
              </p>
            </div>
          );
        })()}
      </div>

      {/* ── Section 5: Application Settings ────────────────────────────────── */}
      <div className="card p-5">
        <h3 className="mb-3 text-base font-semibold text-gray-100">Application</h3>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-300">Theme</p>
            <p className="text-xs text-gray-500">Switch between dark and light mode.</p>
          </div>
          <button className="btn-ghost !py-1.5 text-xs"
            onClick={() => {
              const next = cfg.default_model ? "dark" : "light"; // placeholder toggle
              setCfg({ ...cfg });
            }}>
            {document.documentElement.classList.contains("dark") ? "Dark" : "Light"}
          </button>
        </div>
      </div>
    </Layout>
  );
}
