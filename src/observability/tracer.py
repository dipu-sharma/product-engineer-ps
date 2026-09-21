from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional

from src.domain.entities import TraceEvent, TurnState, TERMINAL_STATES
from src.observability.redactor import redact_secrets


class SafeTracer:
    """Thread-safe and task-safe operational tracer.
    
    Guarantees:
    1. Strictly monotonic sequence ordering.
    2. Deep redaction of secrets, tokens, and authorization headers.
    3. Exclusion of hidden reasoning / CoT internals.
    4. Terminal immutability: Rejects new events after a terminal event is recorded.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: List[TraceEvent] = []
        self._seq_counter = 0
        self._is_sealed = False

    @property
    def is_sealed(self) -> bool:
        with self._lock:
            return self._is_sealed

    def record_event(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        is_terminal: bool = False,
    ) -> Optional[TraceEvent]:
        with self._lock:
            if self._is_sealed:
                # Terminal invariant: No events appear after terminal event
                return None

            self._seq_counter += 1
            now_iso = datetime.now(timezone.utc).isoformat()
            
            clean_payload = redact_secrets(payload or {})
            
            # Ensure hidden reasoning thoughts are not exposed in operational logs
            if "reasoning_content" in clean_payload:
                raw_thought = clean_payload.pop("reasoning_content")
                clean_payload["has_internal_reasoning"] = True
                clean_payload["reasoning_char_count"] = len(str(raw_thought))

            event = TraceEvent(
                seq=self._seq_counter,
                timestamp=now_iso,
                event_type=event_type,
                payload=clean_payload,
            )
            self._events.append(event)

            if is_terminal:
                self._is_sealed = True

            return event

    def get_events(self) -> List[TraceEvent]:
        with self._lock:
            return list(self._events)
