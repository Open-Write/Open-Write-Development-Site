import type { PhaseSpec, RunState } from "../api";

// Horizontal roadmap of pipeline phases with the active phase highlighted and
// completed phases checked off.
export default function PipelinePhaseRoadmap({
  phases,
  state,
}: {
  phases: PhaseSpec[];
  state: RunState | null;
}) {
  const current = state?.current_phase;
  const results = state?.phase_results || {};
  const currentIdx = phases.findIndex((p) => p.key === current);

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {phases.map((p, i) => {
        const done = results[p.key] !== undefined || (currentIdx >= 0 && i < currentIdx);
        const active = p.key === current && state?.active;
        return (
          <div key={p.key} className="flex items-center gap-1.5">
            <div
              className={[
                "flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
                active
                  ? "border-accent-soft bg-accent-soft/15 text-accent"
                  : done
                  ? "border-emerald-600/40 bg-emerald-600/10 text-emerald-300"
                  : "border-edge bg-ink-850 text-gray-500",
              ].join(" ")}
            >
              <span
                className={[
                  "flex h-4 w-4 items-center justify-center rounded-full text-[10px]",
                  active ? "bg-accent-soft text-white" : done ? "bg-emerald-500 text-white" : "bg-ink-700 text-gray-400",
                ].join(" ")}
              >
                {done ? "✓" : i + 1}
              </span>
              {p.label}
              {p.gate && <span className="text-[9px] uppercase tracking-wide opacity-60">gate</span>}
            </div>
            {i < phases.length - 1 && <span className="text-edge">→</span>}
          </div>
        );
      })}
    </div>
  );
}
