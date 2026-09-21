from __future__ import annotations

import threading
from typing import Dict, Optional, Set

from src.domain.entities import TurnState, TerminalState, TERMINAL_STATES
from src.domain.exceptions import InvalidStateTransitionError
from src.observability.tracer import SafeTracer


# Valid forward state transitions
ALLOWED_TRANSITIONS: Dict[TurnState, Set[TurnState]] = {
    TurnState.INITIALIZED: {
        TurnState.EVALUATING_POLICY,
        TurnState.CANCELLED,
        TurnState.FAILED,
    },
    TurnState.EVALUATING_POLICY: {
        TurnState.STREAMING,
        TurnState.REJECTED,
        TurnState.CANCELLED,
        TurnState.TIMED_OUT,
        TurnState.FAILED,
    },
    TurnState.STREAMING: {
        TurnState.COMPLETED,
        TurnState.CANCELLED,
        TurnState.TIMED_OUT,
        TurnState.FAILED,
    },
    # Terminal states have no forward transitions
    TurnState.COMPLETED: set(),
    TurnState.REJECTED: set(),
    TurnState.CANCELLED: set(),
    TurnState.TIMED_OUT: set(),
    TurnState.FAILED: set(),
}


class TurnStateMachine:
    """Thread-safe Finite State Machine governing a single turn.
    
    Guarantees:
    1. Exactly one terminal state wins when completion, cancellation, and timeout race.
    2. Any transition attempt after reaching a terminal state is observably rejected.
    3. Strict enforcement of the allowed transition graph.
    """

    def __init__(self, tracer: SafeTracer) -> None:
        self._tracer = tracer
        self._lock = threading.Lock()
        self._state: TurnState = TurnState.INITIALIZED
        self._terminal_winner: Optional[TerminalState] = None
        self._rejected_transitions: List[Dict[str, Any]] = []

    @property
    def rejected_transitions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._rejected_transitions)

    @property
    def current_state(self) -> TurnState:
        with self._lock:
            return self._state

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return self._terminal_winner is not None

    @property
    def terminal_winner(self) -> Optional[TerminalState]:
        with self._lock:
            return self._terminal_winner

    def transition_to(
        self,
        target: TurnState,
        reason: Optional[str] = None,
    ) -> bool:
        """Attempt to transition to target state.
        
        Returns:
            True if the transition succeeded.
            False if the turn has already reached a terminal state (terminal race loser).
            
        Raises:
            InvalidStateTransitionError if the transition is illegal.
        """
        with self._lock:
            # 1. Terminal race resolution: First terminal state wins permanently
            if self._terminal_winner is not None:
                record = {
                    "attempted_state": target.value,
                    "winning_terminal_state": self._terminal_winner.value,
                    "rejection_reason": reason or "Turn already in terminal state",
                }
                self._rejected_transitions.append(record)
                return False

            # 2. Transition validation
            valid_targets = ALLOWED_TRANSITIONS.get(self._state, set())
            if target not in valid_targets:
                msg = f"Illegal transition from {self._state.value} to {target.value}"
                self._tracer.record_event(
                    event_type="ILLEGAL_TRANSITION_ATTEMPTED",
                    payload={"from": self._state.value, "to": target.value, "error": msg},
                )
                raise InvalidStateTransitionError(msg)

            prev_state = self._state
            self._state = target

            is_becoming_terminal = target in TERMINAL_STATES
            if is_becoming_terminal:
                self._terminal_winner = TerminalState(target.value)

            # Record transition in tracer
            self._tracer.record_event(
                event_type=f"STATE_{target.value}",
                payload={
                    "previous_state": prev_state.value,
                    "new_state": target.value,
                    "reason": reason,
                    "is_terminal": is_becoming_terminal,
                },
                is_terminal=is_becoming_terminal,
            )

            return True
