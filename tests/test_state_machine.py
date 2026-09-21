import pytest
from src.domain.entities import TurnState, TerminalState
from src.domain.exceptions import InvalidStateTransitionError
from src.observability.tracer import SafeTracer
from src.runtime.state_machine import TurnStateMachine


def test_state_machine_happy_path_transitions():
    tracer = SafeTracer()
    fsm = TurnStateMachine(tracer)

    assert fsm.current_state == TurnState.INITIALIZED
    assert not fsm.is_terminal

    assert fsm.transition_to(TurnState.EVALUATING_POLICY)
    assert fsm.current_state == TurnState.EVALUATING_POLICY
    assert not fsm.is_terminal

    assert fsm.transition_to(TurnState.STREAMING)
    assert fsm.current_state == TurnState.STREAMING
    assert not fsm.is_terminal

    assert fsm.transition_to(TurnState.COMPLETED)
    assert fsm.current_state == TurnState.COMPLETED
    assert fsm.is_terminal
    assert fsm.terminal_winner == TerminalState.COMPLETED


def test_state_machine_rejects_illegal_backward_or_skip_transitions():
    tracer = SafeTracer()
    fsm = TurnStateMachine(tracer)

    # Cannot skip directly from INITIALIZED to COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        fsm.transition_to(TurnState.COMPLETED)

    # Valid step
    fsm.transition_to(TurnState.EVALUATING_POLICY)

    # Cannot go backwards
    with pytest.raises(InvalidStateTransitionError):
        fsm.transition_to(TurnState.INITIALIZED)


def test_terminal_state_race_single_winner():
    """AC6: Exactly one valid terminal state wins when transitions race."""
    tracer = SafeTracer()
    fsm = TurnStateMachine(tracer)

    fsm.transition_to(TurnState.EVALUATING_POLICY)
    fsm.transition_to(TurnState.STREAMING)

    # First terminal transition wins
    winner_won = fsm.transition_to(TurnState.CANCELLED, reason="User cancelled")
    assert winner_won is True
    assert fsm.current_state == TurnState.CANCELLED
    assert fsm.terminal_winner == TerminalState.CANCELLED

    # Competing transitions attempting to win after CANCELLED must be rejected (return False)
    assert fsm.transition_to(TurnState.COMPLETED, reason="Model finished just now") is False
    assert fsm.transition_to(TurnState.TIMED_OUT, reason="Timeout fired late") is False
    assert fsm.transition_to(TurnState.FAILED, reason="Provider disconnected") is False

    # Terminal state remains immutable
    assert fsm.current_state == TurnState.CANCELLED
    assert fsm.terminal_winner == TerminalState.CANCELLED

    # Rejection history verifies the competing transitions were rejected observably
    rejected = fsm.rejected_transitions
    assert len(rejected) == 3
    assert rejected[0]["attempted_state"] == "COMPLETED"
    assert rejected[0]["winning_terminal_state"] == "CANCELLED"
    assert rejected[1]["attempted_state"] == "TIMED_OUT"
    assert rejected[2]["attempted_state"] == "FAILED"
