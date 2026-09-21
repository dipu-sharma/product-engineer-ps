import asyncio
import pytest
from src.domain.entities import StreamChunk, TerminalState, TurnRequest
from src.persistence.sqlite_store import SQLitePersistenceStore
from src.policy.rule_policy import RuleBasedPolicyGate
from src.provider.fake_provider import FakeModelProvider
from src.runtime.orchestrator import TurnOrchestrator


@pytest.mark.asyncio
async def test_cancellation_during_streaming():
    """AC3: Provider consumption stops promptly, run becomes cancelled, cannot transition to completed."""
    store = SQLitePersistenceStore()
    policy = RuleBasedPolicyGate()
    # 20 chunks with 0.05s delay each
    provider = FakeModelProvider(
        chunks=[f"Chunk {i}; " for i in range(20)],
        chunk_delay_sec=0.02,
    )
    orchestrator = TurnOrchestrator(policy, provider, store)

    cancel_event = asyncio.Event()
    received_chunks = []

    async def on_chunk(chunk: StreamChunk) -> None:
        received_chunks.append(chunk.content)
        # Cancel after receiving 2 chunks
        if len(received_chunks) == 2:
            cancel_event.set()

    request = TurnRequest(user_input="Write a 20-paragraph story.")
    result = await orchestrator.execute_turn(
        request=request,
        on_chunk=on_chunk,
        cancel_event=cancel_event,
    )

    # 1. State is CANCELLED
    assert result.terminal_state == TerminalState.CANCELLED
    assert result.committed_message_id is None

    # 2. Provider was promptly halted (did NOT emit all 20 chunks)
    assert provider.was_cancelled is True
    assert len(received_chunks) < 10
    assert result.chunk_count == len(received_chunks)

    # 3. Invariant: Conversation History has NO assistant message
    messages = await store.get_conversation_messages(request.conversation_id)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert not any(m.role == "assistant" for m in messages)

    # 4. Forensic Run Record preserves partial output safely
    run_record = await store.get_run_record(result.trace[0].payload["run_id"])
    assert run_record is not None
    assert run_record.terminal_state == TerminalState.CANCELLED
    assert run_record.partial_output == result.accumulated_text
