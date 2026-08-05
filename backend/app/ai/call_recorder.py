"""
call_recorder.py — Append-only per-call record for model invocations.

Satisfies R5 of the retry-policy spec: every model call is logged with
timestamp, phase, chapter, resolved model, provider, failure class,
attempt number, switch info, finish_reason, and elapsed ms.

Records are written as JSONL to <project>/state/call_log.jsonl.
One record per HTTP request (not per phase or per chapter).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

CALL_LOG_FILENAME = "call_log.jsonl"
CALL_LOG_REL = os.path.join("state", CALL_LOG_FILENAME)


@dataclass
class CallRecord:
    """One record per model call."""
    call_id: str               # UUID for concurrent-safe identification (Online uses UUID; Standalone uses per-second counter)
    timestamp: str
    phase: str
    chapter: Optional[int]
    model: str
    provider: str
    failure_class: str         # "ok" or the FailureClass name
    attempt: int               # 1-based attempt number within this call
    switched: bool             # True if this attempt used a switched provider
    switched_to: Optional[str] # provider switched to, if any
    finish_reason: Optional[str]
    elapsed_ms: int
    detail: str = ""           # human-readable detail
    transport_attempts: int = 0
    content_attempts: int = 0
    quality_attempts: int = 0


class CallLogWriter:
    """Append-only JSONL writer for call records.

    Thread-safe for single-process asyncio (the pipeline is sequential).
    Each write is an atomic append (open + write + close).
    """

    def __init__(self, project_path: str):
        self._path = os.path.join(project_path, CALL_LOG_REL)

    def write(self, record: CallRecord) -> None:
        """Append one record to the log."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict]:
        """Read all records (for analysis)."""
        if not os.path.isfile(self._path):
            return []
        records = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records


def make_record(
    phase: str,
    chapter: Optional[int],
    model: str,
    provider: str,
    failure_class: str,
    attempt: int,
    switched: bool = False,
    switched_to: Optional[str] = None,
    finish_reason: Optional[str] = None,
    elapsed_ms: int = 0,
    detail: str = "",
    transport_attempts: int = 0,
    content_attempts: int = 0,
    quality_attempts: int = 0,
) -> CallRecord:
    """Create a CallRecord with a UUID and the current timestamp."""
    return CallRecord(
        call_id=uuid.uuid4().hex[:16],
        timestamp=datetime.now(timezone.utc).isoformat(),
        phase=phase,
        chapter=chapter,
        model=model,
        provider=provider,
        failure_class=failure_class,
        attempt=attempt,
        switched=switched,
        switched_to=switched_to,
        finish_reason=finish_reason,
        elapsed_ms=elapsed_ms,
        detail=detail,
        transport_attempts=transport_attempts,
        content_attempts=content_attempts,
        quality_attempts=quality_attempts,
    )
