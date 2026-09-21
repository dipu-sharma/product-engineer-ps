import pytest
from src.domain.entities import StreamChunk, TerminalState, TurnRequest
from src.persistence.sqlite_store import SQLitePersistenceStore
from src.policy.rule_policy import RuleBasedPolicyGate
from src.provider.fake_provider import FakeModelProvider
from src.runtime.orchestrator import TurnOrchestrator


@pytest.mark.asyncio
async def test_successful_streamed_turn():
    """AC1: Chunks streamed in order, run completes once, and documented records are persisted."""
    store = SQLitePersistenceStore()
    policy = RuleBasedPolicyGate()
    chunks = ["The ", "universe ", "is ", "vast."]
    provider = FakeModelProvider(chunks=chunks, chunk_delay_sec=0.001)
    orchestrator = TurnOrchestrator(policy, provider, store)

    received_chunks = []

    async def chunk_collector(chunk: StreamChunk) -> None:
        received_chunks.append(chunk.content)

    request = TurnRequest(user_input="Tell me about the universe.")
    result = await orchestrator.execute_turn(request, on_chunk=chunk_collector)

    # 1. Verify streaming ordering and completeness
    assert received_chunks == chunks
    assert result.accumulated_text == "The universe is vast."
    assert result.chunk_count == len(chunks)
    assert result.terminal_state == TerminalState.COMPLETED
    assert result.committed_message_id is not None

    # 2. Verify Conversation History contains both User and Assistant messages
    conv_messages = await store.get_conversation_messages(request.conversation_id)
    assert len(conv_messages) == 2
    assert conv_messages[0].role == "user"
    assert conv_messages[0].content == "Tell me about the universe."
    assert conv_messages[1].role == "assistant"
    assert conv_messages[1].content == "The universe is vast."

    # 3. Verify Run Audit Record
    run_record = await store.get_run_record(result.trace[0].payload["run_id"])
    assert run_record is not None
    assert run_record.terminal_state == TerminalState.COMPLETED
    assert run_record.chunk_count == len(chunks)
    assert run_record.error_message is None

    # 4. Terminal invariant: Terminal event is the final event, no events after it
    terminal_events = [
        e for e in result.trace if e.payload.get("is_terminal") is True or e.event_type.startswith("STATE_COMPLETED")
    ]
    assert len(terminal_events) == 1
    assert result.trace[-1] == terminal_events[0]
