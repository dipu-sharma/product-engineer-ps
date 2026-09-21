from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class TurnState(str, Enum):
    INITIALIZED = "INITIALIZED"
    EVALUATING_POLICY = "EVALUATING_POLICY"
    STREAMING = "STREAMING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"


class TerminalState(str, Enum):
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"


TERMINAL_STATES = frozenset({
    TurnState.COMPLETED,
    TurnState.REJECTED,
    TurnState.CANCELLED,
    TurnState.TIMED_OUT,
    TurnState.FAILED,
})


class PolicyDecision(str, Enum):
    ALLOWED = "ALLOWED"
    REJECTED = "REJECTED"


class ChunkType(str, Enum):
    TEXT = "TEXT"
    REASONING = "REASONING"  # CoT / internal thinking (must not leak to trace or conversation)
    USAGE = "USAGE"


@dataclass(frozen=True)
class StreamChunk:
    seq: int
    chunk_type: ChunkType
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceEvent:
    seq: int
    timestamp: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnRequest:
    user_input: str
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout_seconds: float = 10.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationMessage:
    id: str
    conversation_id: str
    turn_id: str
    role: str  # "user" or "assistant"
    content: str
    created_at: str


@dataclass
class RunRecord:
    run_id: str
    turn_id: str
    conversation_id: str
    user_input: str
    terminal_state: TerminalState
    partial_output: str
    chunk_count: int
    error_message: Optional[str]
    created_at: str
    completed_at: str


@dataclass
class TurnResult:
    turn_id: str
    conversation_id: str
    terminal_state: TerminalState
    accumulated_text: str
    chunk_count: int
    error_message: Optional[str]
    trace: List[TraceEvent]
    committed_message_id: Optional[str]
