"""
evaluator — Failure classification, draft metrics, and routing agent.

The Evaluator diagnoses where a pipeline failure lives and routes work back
to the correct station with a patch attached. It operates in two modes:
  Mode A — Draft failure (fires after N proper failed generation attempts)
  Mode B — Revision triage (fires when critic stage concludes with flags)

Submodules:
  classifier  — Class I/II/III failure classification
  metrics     — Deterministic DraftMetrics pre-pass (no LLM calls)
  ledgers     — Intervention ledger + word budget ledger on RunState
  mode_a      — Mode A: draft failure diagnosis and routing
  mode_b      — Mode B: revision triage clustering and tiering
  guards      — Loop guards (all in code, not in prompts)
"""
