import { useState } from "react";
import { api } from "../api";

interface Msg { role: string; content: string; }

// Lightweight chat with the pipeline "companion" that can suggest a creative
// brief / instructions. When it returns suggested_instructions the parent can
// adopt them into the run instructions box.
export default function PipelineChat({
  projectId,
  onSuggest,
}: {
  projectId: string;
  onSuggest?: (instructions: string) => void;
}) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    const next = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setBusy(true);
    setError("");
    try {
      const r = await api.pipelineChat(projectId, { messages: next });
      setMessages([...next, { role: "assistant", content: r.reply }]);
      if (r.suggested_instructions && onSuggest) onSuggest(r.suggested_instructions);
    } catch (e) {
      setError((e as Error).message);
      setMessages(next);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card flex h-full flex-col">
      <div className="border-b border-edge bg-ink-850 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
        Brainstorm with Companion
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <p className="text-sm text-gray-500">
            Describe your story idea and the companion will help shape a creative brief.
            When it proposes instructions, they'll be offered for your run.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : ""}>
            <div
              className={[
                "inline-block max-w-[85%] rounded-lg px-3 py-2 text-sm",
                m.role === "user" ? "bg-accent-soft/20 text-gray-100" : "bg-ink-800 text-gray-300",
              ].join(" ")}
            >
              <span className="whitespace-pre-wrap">{m.content}</span>
            </div>
          </div>
        ))}
        {busy && <div className="text-sm text-gray-500">Thinking…</div>}
      </div>
      {error && <div className="px-4 pb-2 text-xs text-red-300">{error}</div>}
      <div className="flex gap-2 border-t border-edge p-3">
        <input
          className="input"
          placeholder="Describe your story…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          disabled={busy}
        />
        <button className="btn-primary" onClick={send} disabled={busy || !input.trim()}>Send</button>
      </div>
    </div>
  );
}
