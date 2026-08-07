import { useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Msg {
  role: string;
  content: string;
}

// Floating help button + collapsible chat panel. Mounted in Layout so it's
// available on every authenticated page. Modeled on PipelineChat structure.
export default function HelpWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages.
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, busy]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    const next = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setBusy(true);
    setError("");
    try {
      const r = await api.helpChat({ messages: next });
      setMessages([...next, { role: "assistant", content: r.reply }]);
    } catch (e) {
      setError((e as Error).message);
      setMessages(next);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* Floating help button */}
      {!open && (
        <button
          className="fixed bottom-6 right-6 z-50 flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-white shadow-lg transition-all hover:bg-accent-dim hover:scale-105"
          onClick={() => setOpen(true)}
          title="Open-Write Help"
        >
          ?
        </button>
      )}

      {/* Collapsible chat panel */}
      {open && (
        <div className="fixed bottom-6 right-6 z-50 flex h-[32rem] w-96 flex-col rounded-xl border border-edge bg-ink-900 shadow-2xl">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-edge px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-gray-200">Open-Write Help</span>
              <span className="badge bg-accent-soft/15 text-accent text-[10px]">Beta</span>
            </div>
            <button
              className="btn-ghost !py-1 !px-2 text-xs"
              onClick={() => setOpen(false)}
            >
              ✕
            </button>
          </div>

          {/* Messages */}
          <div ref={listRef} className="flex-1 space-y-3 overflow-y-auto p-4">
            {messages.length === 0 && (
              <div className="space-y-3 text-sm text-gray-500">
                <p>
                  Hi! I'm your Open-Write guide. Ask me anything about the app:
                </p>
                <ul className="list-disc space-y-1 pl-4 text-xs">
                  <li>How does the pipeline work?</li>
                  <li>What's in the Output Library?</li>
                  <li>How do I edit chapters?</li>
                  <li>How does version history and restore work?</li>
                  <li>What are the pipeline phases?</li>
                </ul>
                <p className="text-xs italic text-gray-600">
                  For pipeline control, use the Pipeline Chat. For writing feedback, use the Writing Companion.
                </p>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={m.role === "user" ? "text-right" : ""}>
                <div
                  className={[
                    "inline-block max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap",
                    m.role === "user"
                      ? "bg-accent-soft/20 text-gray-100"
                      : "bg-ink-800 text-gray-300",
                  ].join(" ")}
                >
                  {m.content}
                </div>
              </div>
            ))}
            {busy && (
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-gray-500/30 border-t-gray-300" />
                Thinking…
              </div>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="px-4 pb-1 text-xs text-red-300">{error}</div>
          )}

          {/* Input */}
          <div className="flex gap-2 border-t border-edge p-3">
            <input
              className="input"
              placeholder="Ask about Open-Write…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              disabled={busy}
            />
            <button
              className="btn-primary"
              onClick={send}
              disabled={busy || !input.trim()}
            >
              Send
            </button>
          </div>
        </div>
      )}
    </>
  );
}
