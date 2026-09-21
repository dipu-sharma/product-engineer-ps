from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
import uuid

from src.domain.entities import (
    ChunkType,
    PolicyDecision,
    RunRecord,
    StreamChunk,
    TerminalState,
    TurnRequest,
    TurnResult,
    TurnState,
)
from src.domain.exceptions import ProviderExecutionError
from src.observability.tracer import SafeTracer
from src.persistence.interface import PersistenceStore
from src.policy.interface import PolicyGate
from src.provider.interface import ModelProvider
from src.runtime.state_machine import TurnStateMachine


class TurnOrchestrator:
    """Bounded runtime orchestrator managing one streamed conversational turn.
    
    Responsibilities:
    1. Pre-response safety gating via PolicyGate before provider invocation.
    2. Cooperative cancellation and deadline/timeout enforcement.
    3. Provider abstraction streaming with hidden reasoning isolation.
    4. Finite State Machine management with single-winner terminal state resolution.
    5. Dual-boundary persistence semantics (committed assistant turns vs forensic run audit).
    6. Monotonic, secret-redacted operational tracing.
    """

    def __init__(
        self,
        policy_gate: PolicyGate,
        provider: ModelProvider,
        persistence_store: PersistenceStore,
    ) -> None:
        self.policy_gate = policy_gate
        self.provider = provider
        self.persistence_store = persistence_store

    async def execute_turn(
        self,
        request: TurnRequest,
        on_chunk: Optional[Callable[[StreamChunk], Awaitable[None]]] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> TurnResult:
        run_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        tracer = SafeTracer()
        fsm = TurnStateMachine(tracer)

        # 1. Initialize run and record start
        tracer.record_event(
            event_type="RUN_STARTED",
            payload={
                "run_id": run_id,
                "turn_id": request.turn_id,
                "conversation_id": request.conversation_id,
                "timeout_seconds": request.timeout_seconds,
                "metadata": request.metadata,
            },
        )

        # Persist initiating user message into conversation store
        await self.persistence_store.save_user_message(
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            content=request.user_input,
        )

        # 2. Check for early cancellation before policy evaluation
        internal_cancel = asyncio.Event()
        if cancel_event is not None and cancel_event.is_set():
            internal_cancel.set()

        if internal_cancel.is_set():
            fsm.transition_to(TurnState.CANCELLED, reason="Cancelled prior to policy evaluation")
            return await self._finalize_run(
                run_id=run_id,
                request=request,
                fsm=fsm,
                tracer=tracer,
                accumulated_text="",
                chunk_count=0,
                error_message="Cancelled prior to policy evaluation",
                created_at=created_at,
                committed_message_id=None,
            )

        # 3. Pre-response policy evaluation
        fsm.transition_to(TurnState.EVALUATING_POLICY)
        tracer.record_event("POLICY_EVALUATION_STARTED")
        
        policy_result = await self.policy_gate.evaluate(request)
        
        if not policy_result.is_allowed:
            # Policy Rejection: Provider MUST NOT be called.
            # No assistant message is committed to conversation history.
            fsm.transition_to(
                TurnState.REJECTED,
                reason=f"{policy_result.rule_id}: {policy_result.reason}",
            )
            tracer.record_event(
                "POLICY_REJECTED",
                payload={
                    "rule_id": policy_result.rule_id,
                    "reason": policy_result.reason,
                },
            )
            return await self._finalize_run(
                run_id=run_id,
                request=request,
                fsm=fsm,
                tracer=tracer,
                accumulated_text="",
                chunk_count=0,
                error_message=policy_result.reason,
                created_at=created_at,
                committed_message_id=None,
            )

        tracer.record_event(
            "POLICY_APPROVED",
            payload={"rule_id": policy_result.rule_id},
        )

        # 4. Check for cancellation before streaming starts
        if (cancel_event is not None and cancel_event.is_set()) or internal_cancel.is_set():
            fsm.transition_to(TurnState.CANCELLED, reason="Cancelled after policy approval")
            return await self._finalize_run(
                run_id=run_id,
                request=request,
                fsm=fsm,
                tracer=tracer,
                accumulated_text="",
                chunk_count=0,
                error_message="Cancelled after policy approval",
                created_at=created_at,
                committed_message_id=None,
            )

        # 5. Transition to STREAMING
        fsm.transition_to(TurnState.STREAMING)
        tracer.record_event("STREAMING_STARTED")

        accumulated_text = ""
        chunk_count = 0
        error_message: Optional[str] = None
        committed_message_id: Optional[str] = None

        # Link external cancel event to internal cancel
        async def monitor_cancellation() -> None:
            if cancel_event is not None:
                await cancel_event.wait()
                internal_cancel.set()

        cancel_monitor_task = asyncio.create_task(monitor_cancellation())

        try:
            # Enforce timeout deadline strictly using asyncio.timeout
            async with asyncio.timeout(request.timeout_seconds):
                async for chunk in self.provider.stream(request.user_input, internal_cancel):
                    # Prompt cancellation check
                    if internal_cancel.is_set():
                        raise asyncio.CancelledError("Cancellation requested during stream consumption")

                    if chunk.chunk_type == ChunkType.TEXT:
                        accumulated_text += chunk.content
                        chunk_count += 1
                        tracer.record_event(
                            "CHUNK_RECEIVED",
                            payload={
                                "seq": chunk.seq,
                                "chunk_len": len(chunk.content),
                                "metadata": chunk.metadata,
                            },
                        )
                        if on_chunk is not None:
                            await on_chunk(chunk)

                    elif chunk.chunk_type == ChunkType.REASONING:
                        # Private reasoning chunk: sanitized and never exposed to user or trace raw text
                        tracer.record_event(
                            "REASONING_CHUNK_OMITTED",
                            payload={
                                "seq": chunk.seq,
                                "reasoning_content": chunk.content,  # Tracer strips this!
                            },
                        )

            # Reached end of provider stream without error or timeout
            if internal_cancel.is_set():
                fsm.transition_to(TurnState.CANCELLED, reason="Cancelled at stream completion")
            else:
                transition_won = fsm.transition_to(TurnState.COMPLETED)
                if transition_won:
                    # Dual-boundary commitment: Commit assistant message ONLY on COMPLETED
                    msg = await self.persistence_store.commit_assistant_message(
                        conversation_id=request.conversation_id,
                        turn_id=request.turn_id,
                        content=accumulated_text,
                    )
                    committed_message_id = msg.id

        except asyncio.TimeoutError:
            internal_cancel.set()
            error_message = f"Execution exceeded timeout of {request.timeout_seconds}s"
            fsm.transition_to(TurnState.TIMED_OUT, reason=error_message)

        except (asyncio.CancelledError, Exception) as exc:
            internal_cancel.set()
            if isinstance(exc, (asyncio.CancelledError,)):
                error_message = "Turn execution was cancelled"
                fsm.transition_to(TurnState.CANCELLED, reason=error_message)
            elif isinstance(exc, ProviderExecutionError):
                error_message = str(exc)
                fsm.transition_to(TurnState.FAILED, reason=error_message)
            else:
                error_message = f"Unexpected runtime error: {exc}"
                fsm.transition_to(TurnState.FAILED, reason=error_message)

        finally:
            cancel_monitor_task.cancel()
            try:
                await cancel_monitor_task
            except asyncio.CancelledError:
                pass

        return await self._finalize_run(
            run_id=run_id,
            request=request,
            fsm=fsm,
            tracer=tracer,
            accumulated_text=accumulated_text,
            chunk_count=chunk_count,
            error_message=error_message,
            created_at=created_at,
            committed_message_id=committed_message_id,
        )

    async def _finalize_run(
        self,
        run_id: str,
        request: TurnRequest,
        fsm: TurnStateMachine,
        tracer: SafeTracer,
        accumulated_text: str,
        chunk_count: int,
        error_message: Optional[str],
        created_at: str,
        committed_message_id: Optional[str],
    ) -> TurnResult:
        completed_at = datetime.now(timezone.utc).isoformat()
        terminal_state = fsm.terminal_winner or TerminalState.FAILED

        # Save forensic run audit record
        run_record = RunRecord(
            run_id=run_id,
            turn_id=request.turn_id,
            conversation_id=request.conversation_id,
            user_input=request.user_input,
            terminal_state=terminal_state,
            partial_output=accumulated_text,
            chunk_count=chunk_count,
            error_message=error_message,
            created_at=created_at,
            completed_at=completed_at,
        )
        await self.persistence_store.save_run_record(run_record)

        return TurnResult(
            turn_id=request.turn_id,
            conversation_id=request.conversation_id,
            terminal_state=terminal_state,
            accumulated_text=accumulated_text,
            chunk_count=chunk_count,
            error_message=error_message,
            trace=tracer.get_events(),
            committed_message_id=committed_message_id,
        )
